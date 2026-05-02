# Base image (E2B uses Ubuntu 22.04 by default for their base)
FROM ubuntu:22.04

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# 1. Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    unzip \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# 2. Install Node.js 20 (LTS)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs

# 3. Pre-cache common npm packages
# We install them globally or create a dummy project to populate the local cache.
# Installing globally ensures they are available in the path, but for a project
# that runs 'npm install', having them in the npm cache is what speeds it up.
# Here we populate the global cache.

RUN npm install -g \
    vite \
    typescript \
    tailwindcss \
    postcss \
    autoprefixer \
    react \
    react-dom \
    lucide-react \
    clsx \
    tailwind-merge \
    framer-motion \
    date-fns \
    recharts

# 4. Set up a working directory
WORKDIR /home/user/app

# 5. (Optional) Create a dummy package.json and install to prime the local cache
# This is an advanced optimization. For now, the global install + node cache is a good start.
