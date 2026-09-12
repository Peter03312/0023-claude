# 一次性验收服务：仅依赖 Python 标准库，跑完即退出
FROM python:3.11-alpine
WORKDIR /check
COPY acceptance.py ./
CMD ["python", "/check/acceptance.py"]
