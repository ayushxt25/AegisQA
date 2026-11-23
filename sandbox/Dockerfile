# The build command uses --pull=false and the resulting image ID is the only
# accepted runtime reference. Both the base digest and runtime ID are pinned.
FROM python@sha256:840e180ebcc6e5c8efab209c43f5e40fd2af98cb49db5c7103c90539c56bb30e

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --no-cache-dir playwright==1.48.0 \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright
RUN groupadd --gid 65532 aegisqa && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin aegisqa
COPY sandbox/contained_child.py /opt/aegisqa/contained_child.py
COPY agent_runtime /opt/aegisqa/agent_runtime
COPY backend/agent_contracts.py /opt/aegisqa/backend/agent_contracts.py
RUN chown root:root /opt/aegisqa/contained_child.py && chmod 0555 /opt/aegisqa/contained_child.py
RUN chown -R root:root /opt/aegisqa/agent_runtime && chmod -R a+rX /opt/aegisqa/agent_runtime
RUN chown -R root:root /opt/aegisqa/backend && chmod -R a+rX /opt/aegisqa/backend
USER 65532:65532
ENV PYTHONPATH=/opt/aegisqa
ENTRYPOINT ["python", "/opt/aegisqa/contained_child.py"]
