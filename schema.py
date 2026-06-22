import duckdb
from config import DUCKDB_PATH


def create_schema(db_path=DUCKDB_PATH):
    con = duckdb.connect(db_path)

    con.execute("""
        CREATE TABLE IF NOT EXISTS nyfed_survey_raw (
            survey_release_date  DATE NOT NULL,
            survey_due_date      DATE,
            panel_type           VARCHAR NOT NULL,
            question_number      VARCHAR,
            theme                VARCHAR NOT NULL,
            subject_group        VARCHAR,
            subject              VARCHAR NOT NULL,
            question_type        VARCHAR,
            question_mode        VARCHAR,
            question_text        VARCHAR,
            question_tag         VARCHAR NOT NULL,
            value_tag            VARCHAR NOT NULL,
            top_header_value     VARCHAR,
            left_header_value    VARCHAR,
            horizon              VARCHAR,
            horizon_date         DATE,
            bucket_range         VARCHAR,
            bucket_low           DOUBLE,
            bucket_high          DOUBLE,
            aggregation          VARCHAR NOT NULL,
            aggregation_value    DOUBLE,
            source_file          VARCHAR NOT NULL
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS nyfed_survey_bs (
            survey_date      DATE NOT NULL,
            panel_type       VARCHAR NOT NULL,
            variable         VARCHAR NOT NULL,
            horizon_date     DATE NOT NULL,
            pctl25           DOUBLE,
            pctl50           DOUBLE,
            pctl75           DOUBLE,
            respondent_count INTEGER,
            source_file      VARCHAR NOT NULL,
            PRIMARY KEY (survey_date, panel_type, variable, horizon_date)
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS nyfed_survey_runoff (
            survey_date        DATE NOT NULL,
            panel_type         VARCHAR NOT NULL,
            variable           VARCHAR NOT NULL,
            pctl25             VARCHAR,
            pctl50             VARCHAR,
            pctl75             VARCHAR,
            respondent_count   INTEGER,
            source_file        VARCHAR NOT NULL,
            PRIMARY KEY (survey_date, panel_type, variable)
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS fed_balance_sheet (
            observation_date DATE PRIMARY KEY,
            total_assets_bn  DOUBLE,
            treasury_bn      DOUBLE,
            mbs_bn           DOUBLE,
            reserves_bn      DOUBLE,
            source           VARCHAR DEFAULT 'FRED'
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS gdelt_articles (
            url            VARCHAR PRIMARY KEY,
            title          VARCHAR NOT NULL,
            seendate       TIMESTAMP NOT NULL,
            domain         VARCHAR,
            language       VARCHAR,
            sourcecountry  VARCHAR,
            query_keyword  VARCHAR,
            collected_at   TIMESTAMP DEFAULT current_timestamp
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS llm_classifications (
            url                  VARCHAR NOT NULL,
            direction            VARCHAR NOT NULL,
            ensemble_confidence  DOUBLE,
            self_confidence      DOUBLE,
            magnitude            VARCHAR,
            explanation          VARCHAR,
            vote_distribution    VARCHAR,
            model_id             VARCHAR NOT NULL,
            ensemble_k           INTEGER DEFAULT 5,
            processed_at         TIMESTAMP DEFAULT current_timestamp,
            PRIMARY KEY (url, model_id)
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS llm_expectations (
            period         VARCHAR PRIMARY KEY,
            n_increase     INTEGER,
            n_decrease     INTEGER,
            n_uncertain    INTEGER,
            n_not_relevant INTEGER,
            n_total        INTEGER,
            n_relevant     INTEGER,
            f_statistic    DOUBLE
        )
    """)

    con.close()
    print(f"Schema created in {db_path}")


if __name__ == "__main__":
    create_schema()
