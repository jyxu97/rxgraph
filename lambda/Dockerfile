# Lambda Docker container for RxGraph weekly FDA sync.
#
# Build from project root (so COPY paths resolve correctly):
#   docker build -f lambda/Dockerfile -t rxgraph-sync .
#
# Test locally before pushing to ECR:
#   docker run --env-file .env -p 9000:8080 rxgraph-sync
#   curl -XPOST "http://localhost:9000/2015-03-31/functions/function/invocations" -d '{}'
#
# Push to ECR and deploy:
#   aws ecr get-login-password | docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
#   docker tag rxgraph-sync <account>.dkr.ecr.<region>.amazonaws.com/rxgraph-sync:latest
#   docker push <account>.dkr.ecr.<region>.amazonaws.com/rxgraph-sync:latest

FROM public.ecr.aws/lambda/python:3.11

# Copy Lambda handler
COPY lambda/handler.py ${LAMBDA_TASK_ROOT}/handler.py

# Copy ingestion package (openfda_ingest.py + seed_drugs.py)
# handler.py adds /var/task/ingest to sys.path so these are importable directly.
COPY ingest/openfda_ingest.py ${LAMBDA_TASK_ROOT}/ingest/openfda_ingest.py
COPY ingest/seed_drugs.py     ${LAMBDA_TASK_ROOT}/ingest/seed_drugs.py

# Copy shared rxgraph package (rxnorm.py, neo4j_client.py)
COPY rxgraph/ ${LAMBDA_TASK_ROOT}/rxgraph/

# Install Python dependencies
COPY lambda/requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r requirements.txt

# Lambda handler: <module>.<function>
CMD ["handler.handler"]
