FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY backchannel ./backchannel
RUN pip install --no-cache-dir .
COPY scenarios ./scenarios
COPY web ./web
COPY results ./results
RUN python -m backchannel.worker download-files
RUN useradd --create-home lab && chown -R lab:lab /app
USER lab
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "backchannel.web:app", "--host", "0.0.0.0", "--port", "8765", "--no-proxy-headers"]
CMD ["python", "-m", "backchannel.worker", "start"]