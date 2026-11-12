FROM python:3.11-slim-trixie AS python311
FROM python:3.13-slim-trixie AS python313
FROM python:3.14-slim-trixie AS python314
FROM python:3.15-slim-trixie AS python315
FROM node:22-bookworm-slim AS node22
FROM node:24-bookworm-slim AS node24
FROM eclipse-temurin:17-jdk-noble AS jdk17
COPY JavaWorker.java /tmp/JavaWorker.java
RUN mkdir /tmp/java-worker && javac --release 17 --add-modules jdk.jshell -d /tmp/java-worker /tmp/JavaWorker.java
FROM eclipse-temurin:21-jdk-noble AS jdk21
FROM eclipse-temurin:25-jdk-noble AS jdk25

FROM python:3.12-slim-trixie AS native-builder
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && curl --fail --silent --show-error https://apt.llvm.org/llvm-snapshot.gpg.key | gpg --dearmor -o /usr/share/keyrings/llvm.gpg \
    && echo 'deb [signed-by=/usr/share/keyrings/llvm.gpg] https://apt.llvm.org/trixie/ llvm-toolchain-trixie-21 main' > /etc/apt/sources.list.d/llvm.list \
    && apt-get update && apt-get install -y --no-install-recommends g++ libclang-19-dev llvm-19-dev libclang-cpp19-dev libclang-21-dev llvm-21-dev libclang-cpp21-dev
COPY native_worker.cpp /tmp/native_worker.cpp
RUN g++ -std=c++17 /tmp/native_worker.cpp -I/usr/lib/llvm-19/include -L/usr/lib/llvm-19/lib -lclang-cpp -lLLVM-19 -o /tmp/agora-native-worker \
    && g++ -std=c++17 /tmp/native_worker.cpp -I/usr/lib/llvm-21/include -L/usr/lib/llvm-21/lib -lclang-cpp -lLLVM-21 -o /tmp/agora-native-worker-21

FROM python:3.12-slim-trixie
COPY --from=native-builder /usr/share/keyrings/llvm.gpg /usr/share/keyrings/llvm.gpg
COPY --from=native-builder /etc/apt/sources.list.d/llvm.list /etc/apt/sources.list.d/llvm.list
RUN apt-get update && apt-get install -y --no-install-recommends nodejs gcc g++ clang-19 clang-21 libclang-cpp19 libclang-cpp21 kmod util-linux iproute2 ca-certificates maven \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /workspace /sys/fs/cgroup /runtime-modules /opt/agora/java \
    && chown 1000:1000 /workspace
COPY --from=python311 /usr/local /opt/python/3.11
COPY --from=python313 /usr/local /opt/python/3.13
COPY --from=python314 /usr/local /opt/python/3.14
COPY --from=python315 /usr/local /opt/python/3.15
COPY --from=node22 /usr/local/bin/node /opt/node/22/bin/node
COPY --from=node24 /usr/local/bin/node /opt/node/24/bin/node
COPY --from=jdk17 /opt/java/openjdk /opt/jdk/17
COPY --from=jdk21 /opt/java/openjdk /opt/jdk/21
COPY --from=jdk25 /opt/java/openjdk /opt/jdk/25
COPY --from=jdk17 /tmp/java-worker /opt/agora/java
COPY --from=native-builder /tmp/agora-native-worker /usr/local/bin/agora-native-worker
COPY --from=native-builder /tmp/agora-native-worker-21 /usr/local/bin/agora-native-worker-21
COPY workspace.py workspace-crdt.cjs package_manager.py notebook.py notebook_worker.py terminal.py artifact_worker.py cached_javascript.cjs runner.py python_worker.py javascript_worker.cjs program_worker.py java_dependencies.py python_tool.py runtime_options.py /usr/local/lib/
RUN mv /usr/local/lib/runner.py /usr/local/lib/agora-runner.py
COPY init /usr/local/sbin/agora-init
RUN chmod 0755 /usr/local/sbin/agora-init && chmod 0644 /usr/local/lib/*worker* /usr/local/lib/agora-runner.py

# Cache the trusted resolver plugin so fresh VMs only fetch application dependencies.
RUN mkdir -p /opt/agora/maven/repository /tmp/maven-warm \
    && printf '<project><modelVersion>4.0.0</modelVersion><groupId>agora</groupId><artifactId>warm</artifactId><version>1</version></project>' > /tmp/maven-warm/pom.xml \
    && JAVA_HOME=/opt/jdk/21 MAVEN_OPTS='-Xmx192m -XX:+UseSerialGC -XX:ActiveProcessorCount=2' mvn --batch-mode --no-transfer-progress -f /tmp/maven-warm/pom.xml -Dmaven.repo.local=/opt/agora/maven/repository org.apache.maven.plugins:maven-dependency-plugin:3.11.0:build-classpath \
    && rm -rf /tmp/maven-warm

RUN chmod 0755 /usr/local/lib/python_tool.py \
    && mkdir -p /opt/agora/python-tools \
    && for tool in python python3 pip pip3 python3.11 python3.12 python3.13 python3.14 python3.15 pip3.11 pip3.12 pip3.13 pip3.14 pip3.15; do ln -s /usr/local/lib/python_tool.py /opt/agora/python-tools/$tool; done

# Jupyter kernels and rich scientific output are available before first use.
RUN python3 -m pip install --no-cache-dir jupyter-client==8.6.3 ipykernel==6.30.1 matplotlib==3.10.7 pandas==2.3.3
