# Despliegue en VPS (Docker Compose + Caddy + sslip.io)

Arquitectura: `Internet :80/:443 → Caddy (HTTPS automático) → app:8000 (FastAPI)`.
La app solo escucha en `127.0.0.1:8000` del VPS; el público entra únicamente por Caddy.

## 0. Requisitos en el VPS

```bash
docker --version && docker compose version && git --version
# Si falta Docker (instalador oficial):
curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker $USER   # luego cerrar y abrir sesión
```

## 1. Revisar que los puertos 80 y 443 estén libres y permitidos

```bash
# ¿Algo escucha ya en 80/443? (no debe salir nada)
sudo ss -ltnp '( sport = :80 or sport = :443 )'
# ¿Algún contenedor los publica?
docker ps --format '{{.Names}}  {{.Ports}}' | grep -E ':(80|443)->' || echo "ningún contenedor usa 80/443"

# Firewall del sistema
sudo ufw status verbose 2>/dev/null || true          # Ubuntu/Debian
sudo firewall-cmd --list-all 2>/dev/null || true     # RHEL/CentOS/Rocky
sudo iptables -S INPUT | head -30                    # reglas crudas (ej. Oracle Cloud)
```

Si UFW está activo:  `sudo ufw allow 80/tcp && sudo ufw allow 443/tcp`
Si usa firewalld:     `sudo firewall-cmd --permanent --add-service=http --add-service=https && sudo firewall-cmd --reload`

Además, si el VPS es de un proveedor cloud (AWS, GCP, Azure, Oracle, etc.), abre 80 y 443 TCP
en el **security group / firewall del panel** del proveedor.

> Si 80/443 están ocupados por otro servicio (nginx, apache...), hay que detenerlo
> (`sudo systemctl stop nginx`) o avisar para integrar la app detrás de ese proxy.

## 2. Primer despliegue

```bash
git clone <URL-del-repo> ~/hacktaton && cd ~/hacktaton
cp .env.example .env && chmod 600 .env
nano .env     # DEEPSEEK_API_KEY=...   APP_HOST=<IP-con-guiones>.sslip.io   (ej. 203-0-113-5.sslip.io)
./deploy.sh
```

`deploy.sh` hace: `git pull` → `docker compose build` → `up -d` → health check interno → health check público.
La primera vez Caddy tarda ~30–60 s en obtener el certificado de Let's Encrypt.

## 3. Plan B: HTTP directo en la IP (si el certificado falla)

```bash
sed -i 's/^CADDY_MODE=.*/CADDY_MODE=http/' .env && ./deploy.sh
# URL: http://<IP>
```
Para volver a HTTPS: `CADDY_MODE=https` y `./deploy.sh`.
Diagnóstico del certificado: `docker compose logs --tail 80 caddy` (el puerto 80 debe ser accesible
desde Internet para el desafío de Let's Encrypt).

## 4. Redesplegar (un solo comando, desde tu PC)

```bash
ssh <usuario>@<IP> 'cd ~/hacktaton && ./deploy.sh'
```

## 5. Verificar desde fuera del servidor

```bash
python scripts/verify_remote.py https://<IP-con-guiones>.sslip.io
```
Comprueba `/health`, lanza el caso de ejemplo, aprueba la acción sensible y muestra la traza.

### Si tu red bloquea sslip.io

Algunas redes (filtros DNS como Fortinet FortiGuard) bloquean `*.sslip.io`: el host resuelve a
otra IP y aparece un certificado "no confiable" o un 403. El servidor no tiene la culpa.
Comprobar: `nslookup <IP-con-guiones>.sslip.io` debe devolver la IP del VPS.

- Verificar igual saltando el DNS local (el certificado se valida normal):
  `python scripts/verify_remote.py https://<host> --ip <IP>`
- Para la demo en esa red: usar otra red (ej. datos del celular) o el plan B por IP.

## Seguridad

- `.env` está en `.gitignore` y `.dockerignore`: nunca entra al repo ni a la imagen.
  La clave se inyecta en tiempo de ejecución (`env_file`).
- Los certificados se guardan en el volumen `caddy_data` (no se pierden al redesplegar).
