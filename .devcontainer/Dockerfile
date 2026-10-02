FROM ubuntu:20.04

# Prevent interactive timezone/region prompts during apt-get
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    wget \
    make \
    git \
    xz-utils \
    gcc-7 \
    g++-7 \
    && rm -rf /var/lib/apt/lists/*

# Set gcc-7 and g++-7 as the default compilers
RUN update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-7 100 \
    && update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-7 100
    
RUN gcc --version && g++ --version