# The notebook control server CRAFT's agent reads notebooks through; it never runs user code itself.
FROM python:3.12.13-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36
RUN pip install --no-cache-dir jupyter-mcp-server==2.2.4 \
    && useradd --uid 1000 --create-home control
USER control
EXPOSE 4040
CMD ["jupyter-mcp-server", "start", "--transport", "streamable-http", "--host", "0.0.0.0", \
     "--port", "4040", "--start-new-code-sandbox", "False", "--document-provider", "jupyter", \
     "--jupyterlab", "False", "--reconnect-interval", "0", \
     "--execution-timeout", "120", "--max-execution-timeout", "120"]
