-- Runs once when the Postgres volume is first created: a separate database for n8n's own data.
CREATE DATABASE n8n OWNER jobs;
