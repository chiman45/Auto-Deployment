FROM nginx:alpine

# Copy a simple HTML file
RUN echo '<html><body><h1>Docker Test Success!</h1><p>This is a test container running nginx.</p></body></html>' > /usr/share/nginx/html/index.html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
