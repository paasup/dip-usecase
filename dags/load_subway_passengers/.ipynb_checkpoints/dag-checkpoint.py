from datetime import datetime, timedelta
from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.spark_kubernetes import SparkKubernetesOperator
from airflow.providers.cncf.kubernetes.sensors.spark_kubernetes import SparkKubernetesSensor


dag = DAG(
   dag_id="load_subway_passengers",
   default_args={'max_active_runs': 1},
   description='submit deltalake example as sparkApplication on kubernetes',
   schedule_interval=timedelta(days=1),
   start_date=datetime(2025, 6, 1),
   catchup=False
)

t1 = SparkKubernetesOperator(
   task_id='load_subway_passengers',
   namespace="demo01-spark-job",
   application_file="./spark-app.yaml",
   params={"from_date": False, "to_date": False},
   dag=dag
)

t1 
