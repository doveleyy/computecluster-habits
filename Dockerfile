FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY services/habit_tracker/requirements.txt /tmp/requirements.txt
COPY vendor/home_platform_client-0.1.1-py3-none-any.whl /tmp/
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt /tmp/home_platform_client-0.1.1-py3-none-any.whl \
    && rm /tmp/requirements.txt /tmp/home_platform_client-0.1.1-py3-none-any.whl \
    && groupadd --gid 10001 habits \
    && useradd --uid 10001 --gid habits --no-create-home --shell /usr/sbin/nologin habits \
    && mkdir /data && chown habits:habits /data
COPY services/__init__.py services/__init__.py
COPY services/habit_tracker/*.py services/habit_tracker/
COPY services/habit_tracker/templates services/habit_tracker/templates
COPY services/habit_tracker/static services/habit_tracker/static
USER 10001:10001
EXPOSE 8100
CMD ["python", "-m", "services.habit_tracker"]
