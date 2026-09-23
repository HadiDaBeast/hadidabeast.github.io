FROM nginx:1.27-alpine

# Remove default nginx config
RUN rm -rf /etc/nginx/conf.d/default.conf

# Copy nginx config directly (no envsubst needed — values are hardcoded)
COPY nginx.conf /etc/nginx/nginx.conf

# Copy static files
COPY www/ /usr/share/nginx/html/

# Ensure tmp dirs exist for non-root operation
RUN mkdir -p /tmp/client_body /tmp/proxy_temp /tmp/fastcgi_temp /tmp/uwsgi_temp /tmp/scgi_temp && \
    chown -R nobody:nobody /tmp/client_body /tmp/proxy_temp /tmp/fastcgi_temp /tmp/uwsgi_temp /tmp/scgi_temp && \
    chown -R nobody:nobody /var/cache/nginx /var/log/nginx && \
    chmod -R 755 /var/cache/nginx /var/log/nginx

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://localhost:8080/healthz || exit 1

USER nobody

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
