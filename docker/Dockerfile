FROM ubuntu:artful
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    gcc
CMD pip3 install -e . && /bin/bash