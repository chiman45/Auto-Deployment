# Use official Python base image
FROM python:3.10-slim

# Set working directory inside the container
WORKDIR /app

# Copy dependency file first for caching
COPY requirements.txt .

# Install dependencies
RUN apt-get update && apt-get install -y git && apt-get clean
RUN pip install --no-cache-dir -r requirements.txt
# Copy the entire project
COPY . .

# Set environment variables (avoid putting secrets directly here)
ENV PYTHONUNBUFFERED=1

# Load environment variables from .env at runtime
# You can use Docker --env-file .env when running

# Command to run the script
CMD ["python", "main.py"]

#docker run -it --name agent_container myagent:latest
