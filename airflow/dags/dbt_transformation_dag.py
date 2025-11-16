from airflow import DAG
from airflow.operators.bash import BashOperator
from datetime import datetime

with DAG(
    dag_id='dbt_transformation_dag',
    start_date=datetime(2023, 1, 1),
    schedule_interval='@daily',
    catchup=False,
    tags=['dbt', 'transformation'],
) as dag:
    dbt_debug = BashOperator(
        task_id='dbt_debug',
        bash_command='cd /home/joose/fitted/dbt/fitted_dbt && dbt debug',
    )

    dbt_deps = BashOperator(
        task_id='dbt_deps',
        bash_command='cd /home/joose/fitted/dbt/fitted_dbt && dbt deps',
    )

    dbt_seed = BashOperator(
        task_id='dbt_seed',
        bash_command='cd /home/joose/fitted/dbt/fitted_dbt && dbt seed',
    )

    dbt_run = BashOperator(
        task_id='dbt_run',
        bash_command='cd /home/joose/fitted/dbt/fitted_dbt && dbt run',
    )

    dbt_debug >> dbt_deps >> dbt_seed >> dbt_run