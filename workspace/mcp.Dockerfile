# The notebook control server CRAFT's agent reads notebooks through; it never runs user code itself.
# It runs beside JupyterLab in the person's pod (deploy/jupyterhub/values.yaml), listening on the
# pod's loopback only: CRAFT reaches it through JupyterLab's proxy, behind the Hub's sign-in. It
# reads notebooks with the server's own Hub token, which JupyterLab writes to /run/craft-control.
FROM python:3.12.13-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36
# jupyter-mcp-server (mcp-requirements.in) and everything it needs, pinned with hashes; the
# command at the top of mcp-requirements.txt regenerates it.
COPY mcp-requirements.txt /tmp/mcp-requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/mcp-requirements.txt \
    && rm /tmp/mcp-requirements.txt \
    && useradd --uid 1000 --create-home control
USER control
EXPOSE 4040
CMD ["sh", "-c", "until [ -s /run/craft-control/token ] && [ -s /run/craft-control/url ]; do sleep 1; done; \
JUPYTER_TOKEN=$(cat /run/craft-control/token) JUPYTER_URL=$(cat /run/craft-control/url) \
exec jupyter-mcp-server start --transport streamable-http --host 127.0.0.1 \
     --port 4040 --start-new-code-sandbox False --document-provider jupyter \
     --jupyterlab False --reconnect-interval 0 --insecure-mcp-noauth \
     --execution-timeout 120 --max-execution-timeout 120"]
