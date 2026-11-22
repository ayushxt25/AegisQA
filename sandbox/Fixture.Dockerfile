FROM python@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534

RUN groupadd --gid 65532 aegisqa && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin aegisqa
COPY sandbox/fixture_server.py /opt/aegisqa/fixture_server.py
RUN chown root:root /opt/aegisqa/fixture_server.py && chmod 0555 /opt/aegisqa/fixture_server.py
USER 65532:65532
ENTRYPOINT ["python", "/opt/aegisqa/fixture_server.py"]
