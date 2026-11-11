"""Close the known interrupted v1 integration lease in its isolated database."""

from sqlalchemy import select

from tariff_agent.persistence import jobs
from tariff_agent.store import Store


def main():
    store = Store("data/private/qdrant-agent-integration.db")
    with store.connect() as db:
        row = db.execute(
            select(jobs.c.id, jobs.c.token, jobs.c.status).where(jobs.c.idem == "qdrant-real-workflow-v1")
        ).first()
    if row and row.status == "running":
        store.finish(row.id, row.token, "failed", error="CandidateScopeValidationRejected")
        print("Preserved the rejected v1 integration as a failed attempt")


if __name__ == "__main__":
    main()
