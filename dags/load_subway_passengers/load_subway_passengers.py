#!/usr/bin/env python
# coding: utf-8

# In[7]:


from pyspark.sql import SparkSession
from pyspark.conf import SparkConf
from pyspark.sql.functions import lit, substring, col
from delta.tables import DeltaTable 
import os
from dotenv import load_dotenv
import psycopg2 
import boto3
from botocore.exceptions import ClientError


# In[2]:


# input parameters
WK_YM = '202502'


# In[8]:


def create_spark_session(app_name,  notebook_name, notebook_namespace, s3_access_key, s3_secret_key, s3_endpoint):

    conf = SparkConf()
    # Spark driver, executor 설정
    conf.set("spark.submit.deployMode", "client")
    conf.set("spark.executor.instances", "1")
    conf.set("spark.executor.memory", "1G")
    conf.set("spark.driver.memory", "1G")
    conf.set("spark.executor.cores", "1")
    conf.set("spark.kubernetes.namespace", notebook_namespace)
    conf.set("spark.kubernetes.container.image", "paasup/spark:3.5.2-java17-python3.11-2")
    conf.set("spark.kubernetes.authenticate.driver.serviceAccountName", "default-editor")
    conf.set("spark.kubernetes.driver.pod.name", os.environ["HOSTNAME"])
    conf.set("spark.driver.bindAddress", "0.0.0.0")
    conf.set("spark.driver.host", notebook_name+ "-headless." + notebook_namespace + ".svc.cluster.local")
    conf.set("spark.driver.port", "51810")        
    conf.set("spark.broadcast.port", "51811")     
    conf.set("spark.blockManager.port", "51812")

    # s3, delta, postgresql 사용 시 필요한 jar 패키지 설정
    jar_list = ",".join([
    "org.apache.hadoop:hadoop-common:3.3.4",
    "org.apache.hadoop:hadoop-aws:3.3.4",
    "com.amazonaws:aws-java-sdk:1.11.655", 
    "io.delta:delta-spark_2.12:3.3.1", # Keep if you might use Delta Lake for other operations
    "org.postgresql:postgresql:42.7.2" # Added for PostgreSQL
    ])
    conf.set("spark.jars.packages", jar_list)  

    # s3 세팅
    conf.set("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
    conf.set('spark.hadoop.fs.s3a.path.style.access', 'true')
    conf.set('spark.hadoop.fs.s3a.connection.ssl.enabled', 'true')
    conf.set("spark.hadoop.fs.s3a.aws.credentials.provider", "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider")
    conf.set('spark.hadoop.fs.s3a.access.key', s3_access_key)
    conf.set('spark.hadoop.fs.s3a.secret.key', s3_secret_key)
    conf.set('spark.hadoop.fs.s3a.endpoint', s3_endpoint)
    
    ### ssl 검증 비활성화
    conf.set("spark.driver.extraJavaOptions", "-Dcom.amazonaws.sdk.disableCertChecking=true")
    conf.set("spark.executor.extraJavaOptions", "-Dcom.amazonaws.sdk.disableCertChecking=true")

    # deltalake 세팅
    conf.set("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
    conf.set("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog") 

    # --- SparkSession 빌드 ---
    spark = SparkSession.builder \
        .appName(app_name) \
        .config(conf=conf) \
        .getOrCreate()

    return spark


# In[9]:


def move_s3_file(s3_config, source_key, dest_key):
    """
    boto3를 사용하여 S3 내에서 파일을 이동(복사 후 삭제)합니다.
    """
    s3_client = boto3.client(
        's3',
        aws_access_key_id=s3_config['access_key'],
        aws_secret_access_key=s3_config['secret_key'],
        endpoint_url=s3_config['endpoint'],
        verify=False # 자체 서명 인증서 사용 시 필요할 수 있음
    )
    try:
        copy_source = {'Bucket': s3_config['bucket'], 'Key': source_key}
        print(f"Copying S3 object from {source_key} to {dest_key}...")
        s3_client.copy_object(CopySource=copy_source, Bucket=s3_config['bucket'], Key=dest_key)
        print(f"Deleting source S3 object: {source_key}...")
        s3_client.delete_object(Bucket=s3_config['bucket'], Key=source_key)
        print(f"Successfully moved {source_key} to {dest_key}.")
        return True
    except ClientError as e:
        print(f"Error moving S3 file: {e}")
        return False
    except Exception as e:
        print(f"An unexpected error occurred during S3 move: {e}")
        return False


# In[10]:


def subway_data_append(spark, s3_config, pg_config):  #pg_config, pg_table, pg_url, pg_properties, delete_sql, insert_sql):
    """
    CSV--> DELTA --> POSTGRESQL 처리하는 메인 파이프라인
    """
    pg_url = f"jdbc:postgresql://{pg_config['host']}:{pg_config['port']}/{pg_config['dbname']}"
    pg_properties = {
        "user": pg_config['user'],
        "password": pg_config['password'],
        "driver": "org.postgresql.Driver"
    }

    print(f" bucket name is {s3_config['bucket']}")
    csv_source_key = f"datasource/CARD_SUBWAY_MONTH_{WK_YM}.csv"
    csv_archive_key = f"archive/CARD_SUBWAY_MONTH_{WK_YM}.csv"
    csv_s3_path = f"s3a://{s3_config['bucket']}/{csv_source_key}"
    delta_s3_path = f"s3a://{s3_config['bucket']}/{s3_config['delta_path']}"
    
    try:    
        print(f"\n--- Starting processing for {pg_config['table']} during '{WK_YM}' ---")

        # --- 단계 1: S3 CSV 읽기 ---
        print(f"Reading CSV from {csv_s3_path}...")
        # CSV 스키마나 옵션(overwrite, inferSchema...)은 소스 파일에 맞게 조정
        csv_df = spark.read.option("header", "true").option("overwriteSchema", "true").csv(csv_s3_path)
        # '사용일자' 컬럼이 YYYYMMDD 형식이라고 가정
        # 필요시 yyyymm 컬럼을 추가하거나 기존 컬럼을 확인
        # csv_df = csv_df.withColumn("operation_month", lit(yyyymm)) # 필요시 추가
        csv_df.createOrReplaceTempView("subway_data")
        print(f"CSV for {WK_YM} loaded. Count: {csv_df.count()}")
        csv_df.show(5)

        # --- 단계 2: Delta Lake에 삭제 후 삽입 (Spark SQL 사용) ---
        print(f"Deleting old data for {WK_YM} from Delta table: {delta_s3_path}")
        
        if DeltaTable.isDeltaTable(spark, delta_s3_path):
            # '사용일자' 컬럼 형식에 맞게 WHERE 조건 수정
            print(f" Delta Table {delta_s3_path} 있음") 
            delete_delta_sql = f"delete from delta.`{delta_s3_path}` where `사용일자`  like '{WK_YM}'||'%' "
            delta_mode = "append"
            try:
                spark.sql(delete_delta_sql)
                print(f"Deletion from Delta complete. Inserting new data for {WK_YM}...")
            except Exception as e:
                print(f"Skip on error occurred during Delta DELETE, even though table exists: {e}")
                raise # 예상치 못한 오류는 다시 발생시킴         
        else:
            print(f" Delta Table {delta_s3_path} 없음") 
            delta_mode = "overwrite"
        
        # DataFrame API로 쓰기 (SQL INSERT INTO도 가능하지만, DF API가 S3 쓰기에 더 일반적)
        csv_df.write.format("delta").mode(delta_mode).save(delta_s3_path)
        print(f"Successfully wrote data to Delta table {delta_s3_path}.")
        
        # --- 단계 3: S3 CSV 파일 이동 ---
        print(f"Archiving S3 CSV file...")
        move_s3_file(s3_config, csv_source_key, csv_archive_key)
        

        # --- 단계 4: PostgreSQL에 삭제 후 삽입 ---   
        pg_table_name = pg_config['table']
        print(f"Deleting old data for {WK_YM} from PostgreSQL table: {pg_table_name}")        
        conn = None
        cursor = None
        conn = psycopg2.connect (
                    host=pg_config['host'],
                    port=pg_config['port'],
                    dbname=pg_config['dbname'],
                    user=pg_config['user'],
                    password=pg_config['password'] )
        cursor = conn.cursor()

        delete_sql = f" delete from {pg_table_name} where use_date like  '{WK_YM}'||'%' "
        print(f"Executing DELETE query: {delete_sql}")
        cursor.execute(delete_sql)
        deleted_rows = cursor.rowcount
        if cursor: cursor.close()
        if conn: 
            conn.commit()
            conn.close()
        print(f"Successfully executed DELETE query. {deleted_rows} rows deleted.")    

        print(f"Inserting data for {WK_YM} into PostgreSQL table: {pg_table_name}")
         # 데이터를 가공할 Spark SQL 쿼리
        insert_sql = f"""
                        select  `사용일자` use_date
                           ,`노선명` line_no
                           ,`역명`  station_name
                           ,cast(`승차총승객수` as int) pass_in
                           ,cast(`하차총승객수` as int) pass_out
                           ,substring(`등록일자`,1,8) reg_date
                        from delta.`{delta_s3_path}`
                        where `사용일자` like '{WK_YM}'||'%'
                    """      
        print(insert_sql) 
        delta_df = spark.sql(insert_sql)
        delta_df.write \
            .jdbc(url=pg_url,
                  table=pg_table_name,
                  mode="append",
                  properties=pg_properties)
        print(f"Successfully wrote data to PostgreSQL table {pg_table_name}.")

        print(f"--- Processing for {WK_YM} completed successfully! ---")

    except Exception as e:
        print(f"An error occurred during processing {WK_YM}: {e}")
        import traceback
        traceback.print_exc()


# In[11]:


# --- 메인 실행 블록 ---
if __name__ == "__main__":
    # input parameters
    WK_YM = '202504'
    
    # Pod 내의  .env 파일에서 환경변수 read     
    dotenv_path = './config/.env'
    load_dotenv(dotenv_path=dotenv_path)    
    
    # --- APP구성 변수 설정  ---
    APP_NAME = 'load_subway_passengers'
    NOTEBOOK_NAME      = "test"
    NOTEBOOK_NAMESPACE = "demo01-kf"    
    S3_CONFIG = {
        "access_key": os.getenv("S3_ACCESS_KEY"),
        "secret_key": os.getenv("S3_SECRET_KEY"),
        "endpoint": os.getenv("S3_ENDPOINT_URL"),
        "bucket":  os.getenv("S3_BUCKET_NAME"),
        "delta_path": "deltalake/subway_passengers" # S3 버킷 내 Delta target 테이블 경로
    }

    PG_CONFIG = {
        "host": os.getenv("PG_HOST"),
        "port": os.getenv("PG_PORT", "5432"), # 기본값 5432 지정
        "dbname": os.getenv("PG_DB"),
        "user": os.getenv("PG_USER"),
        "password": os.getenv("PG_PASSWORD"),
        "table": "subway_passengers" # PostgreSQL target 테이블 이름
    }
    # SparkSession 생성
    spark = create_spark_session(
        app_name=APP_NAME,
        notebook_name=NOTEBOOK_NAME,
        notebook_namespace=NOTEBOOK_NAMESPACE,
        s3_access_key=S3_CONFIG['access_key'],
        s3_secret_key=S3_CONFIG['secret_key'],
        s3_endpoint=S3_CONFIG['endpoint']
        # app_name은 기본값을 사용하므로 전달하지 않아도 됩니다.
    )   
    
    # 데이터 처리 실행
    subway_data_append(spark, S3_CONFIG, PG_CONFIG)
    

    # # SparkSession 종료
    # spark.stop()
    # print("Spark session stopped.")


# In[22]:


spark.stop()

