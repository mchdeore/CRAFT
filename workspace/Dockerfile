# JupyterLab and the CRAFT package share one user workspace image.
# The Office preview front end is built once in its own stage; only its output is copied.
FROM python:3.12.13-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36 AS preview
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir jupyterlab==4.6.4
WORKDIR /build/labextension
COPY labextension/ ./
RUN npm install --no-audit --no-fund --ignore-scripts && npm run build

FROM python:3.12.13-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36
COPY --from=ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 /uv /bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/notebook \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

# LibreOffice renders Office previews and page images; the fonts are metric-compatible
# stand-ins (Carlito for Calibri, Caladea for Cambria, Liberation for Arial, Times, Courier).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
        poppler-utils fonts-crosextra-carlito fonts-crosextra-caladea fonts-liberation2 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 workspace \
    && useradd --uid 1000 --gid 1000 --create-home workspace \
    && mkdir /workspace \
    && chown workspace:workspace /workspace

WORKDIR /opt/craft-user-workspace
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY --from=preview /build/src/craft_preview/labextension ./src/craft_preview/labextension
# The external MCP2 control service runs outside this user image.
# Install locked wheels, including Setuptools, before building the local package.
RUN uv sync --locked --extra all --no-dev --group build --no-editable \
        --no-install-project --no-build --no-cache \
    && uv sync --locked --extra all --no-dev --group build --no-editable \
        --no-build-isolation-package craft-user-workspace --no-cache
# Load the outbound guard at interpreter start; it installs only where CRAFT_GUARD=1,
# which the kernel specification sets. The Jupyter server runs without it.
RUN /opt/notebook/bin/python -c "import json, pathlib, site; \
site_dir = pathlib.Path(site.getsitepackages()[0]); \
(site_dir / 'craft-guard.pth').write_text('import craft_guard\n'); \
spec = pathlib.Path('/opt/notebook/share/jupyter/kernels/python3/kernel.json'); \
data = json.loads(spec.read_text()); data['env'] = {**data.get('env', {}), 'CRAFT_GUARD': '1'}; \
spec.write_text(json.dumps(data, indent=1) + '\n')"
COPY skills ./skills
COPY jupyter_server_config.py ./jupyter_server_config.py
RUN mkdir -p /opt/notebook/share/jupyter/labextensions \
    && cp -R src/craft_preview/labextension /opt/notebook/share/jupyter/labextensions/craft-preview

ENV PATH=/opt/notebook/bin:$PATH \
    USER_ROOT=/workspace \
    PYTHONDONTWRITEBYTECODE=1 \
    JUPYTER_RUNTIME_DIR=/tmp/jupyter-runtime \
    JUPYTER_CONFIG_DIR=/tmp/jupyter-config \
    IPYTHONDIR=/tmp/ipython \
    MPLCONFIGDIR=/tmp/matplotlib \
    JUPYTER_DATA_DIR=/tmp/jupyter-data
USER workspace:workspace
WORKDIR /workspace
EXPOSE 8888
# No CMD: the Hub (singleuser.cmd in deploy/jupyterhub/values.yaml) and the notebook Job each pass
# their own command.
