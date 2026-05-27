# simple-chat-ansible

Ansible project for deploying a lightweight group chat application on Ubuntu 24.04.

## Overview

This project deploys a simple self-hosted group chat system using a two-VM architecture.

```text
Internet
  |
Cloudflare Tunnel
  |
chat-app01
  - nginx
  - Flask
  - cloudflared
  |
chat-data01
  - MariaDB
  - MinIO
Features
User registration
User ID based login
Password reset email via Brevo SMTP
Group creation
Member invitation
Member removal
Group leave
Text chat
Image upload
Screenshot paste upload
Image modal preview
Message deletion
Lightweight polling-based auto update
MariaDB persistence
MinIO object storage
Cloudflare Tunnel external publishing
Tech Stack
Ansible
Docker Compose
nginx
Flask
MariaDB
MinIO
Cloudflare Tunnel
Brevo SMTP
Tested Environment
Ubuntu Server 24.04
Ansible 2.14+
Docker / Docker Compose v2
MariaDB 11 container
MinIO container
Python 3.12 Flask app container
nginx container
Directory Structure
simple-chat-ansible/
├── ansible.cfg
├── inventory.ini
├── site.yml
├── group_vars/
│   └── all.yml.example
└── roles/
    ├── common/
    ├── docker/
    ├── data_stack/
    ├── chat_stack/
    └── cloudflared/
Setup

Copy the example variables file:

cp group_vars/all.yml.example group_vars/all.yml

Edit group_vars/all.yml and set your own values.

flask_secret_key: "CHANGE_ME_SECRET_KEY"

db_password: "CHANGE_ME_DB_PASSWORD"
db_root_password: "CHANGE_ME_DB_ROOT_PASSWORD"

minio_access_key: "CHANGE_ME_MINIO_ACCESS_KEY"
minio_secret_key: "CHANGE_ME_MINIO_SECRET_KEY"

mail_username: "CHANGE_ME_BREVO_SMTP_LOGIN"
mail_password: "CHANGE_ME_BREVO_SMTP_KEY"
mail_default_sender: "no-reply@example.com"

cloudflared_tunnel_uuid: "CHANGE_ME_TUNNEL_UUID"
cloudflared_hostname: "chat.example.com"
Inventory Example
[chat_app]
target-chat-app01.example.jp

[chat_data]
target-chat-data01.example.jp

[chat_all:children]
chat_app
chat_data

[chat_all:vars]
ansible_user=ansible
ansible_become=true
Deploy

Check syntax:

ansible-playbook site.yml --syntax-check

Deploy all roles:

ansible-playbook site.yml

Deploy only the data stack:

ansible-playbook site.yml --limit chat_data

Deploy only the app stack:

ansible-playbook site.yml --limit chat_app
Cloudflare Tunnel

Create a Cloudflare Tunnel manually first.

Place the tunnel credentials JSON on the app host:

/etc/cloudflared/<TUNNEL_UUID>.json

Then configure these variables in group_vars/all.yml:

cloudflared_enable: true
cloudflared_tunnel_name: "simple-chat"
cloudflared_tunnel_uuid: "<TUNNEL_UUID>"
cloudflared_credentials_file: "/etc/cloudflared/<TUNNEL_UUID>.json"
cloudflared_hostname: "chat.example.com"
cloudflared_service: "http://localhost:8080"
Security Notes

Do not commit secrets.

The following files are intentionally ignored:

group_vars/all.yml
.env
app.env
Cloudflare credentials JSON
certificate/key files

Before publishing, check for secrets:

find . -type f \( -name "*.env" -o -name "*.json" -o -name "*.pem" -o -name "*.key" -o -name "*.crt" \) -print
License

MIT


---

## 3. LICENSE を作成

```bash
vi LICENSE
MIT License

Copyright (c) 2026 Masahiro Hashimoto

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files, to deal in the Software
without restriction, including without limitation the rights to use, copy,
modify, merge, publish, distribute, sublicense, and/or sell copies of the
Software, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
