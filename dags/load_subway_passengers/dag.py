
from datetime import datetime, timedelta
from airflow import DAG
from airflow.models.param import Param
from airflow.providers.cncf.kubernetes.operators.spark_kubernetes import SparkKubernetesOperator
from airflow.providers.cncf.kubernetes.sensors.spark_kubernetes import SparkKubernetesSensor


dag = DAG(
   dag_id="load_subway_passengers",
   default_args={'max_active_runs': 1},
   description='submit deltalake example as sparkApplication on kubernetes',
   schedule_interval=timedelta(days=1),
   start_date=datetime(2025, 6, 1),
   catchup=False,
   # DAG 레벨에서 파라미터 정의
   params={
       "WK_YM": Param(
           default="202504",
           type="string",
           description="작업 년월 (YYYYMM 형식)"
       )
   }
)

t1 = SparkKubernetesOperator(
   task_id='load_subway_passengers',
   namespace="demo01-spark-job",
   application_file="./spark-app.yaml",
   # 환경변수나 template_fields를 통해 파라미터 전달
   dag=dag
)
t1