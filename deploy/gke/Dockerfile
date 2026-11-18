# Any abstract profile on top of the published harness image. up.sh builds it
# from a staged copy of the profile directory (this file is copied next to it).
ARG HARNESS_IMAGE=dkatz23238/abstract-business-harness:0.2.1
FROM ${HARNESS_IMAGE}

USER root
COPY --chown=harness:harness . /harness-profile
RUN if [ -f /harness-profile/requirements.txt ]; then \
      uv pip install --python /app/.venv/bin/python -r /harness-profile/requirements.txt; \
    fi \
    && rm -f /harness-profile/Dockerfile
USER harness
ENV HARNESS_DATA_ROOT=/harness-data
