FROM registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x@sha256:d2da8a7b0c88c3b468e29014b2ada9ef8274f98c976ec456904c5e2bae5aa54c
USER root
COPY requirements-lock.txt /tmp/requirements-lock.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements-lock.txt
USER 1000
WORKDIR /workspace
CMD ["python", "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "38900", "--workers", "1", "--no-server-header"]
