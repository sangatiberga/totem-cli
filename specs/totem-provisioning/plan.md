# Plano Técnico de Implementação: Totem CLI (Fase 2 — plan.md)

## 1. Visão Geral da Abordagem Técnica

A aplicação será dividida em uma arquitetura de camadas com responsabilidades estritas e desacopladas:
1. **Camada de Apresentação (TUI):** Desenvolvida em Textual (Python), provê um wizard de passos guiados, com feedback em tempo real (barras de progresso, confirmações em modal e cards de status).
2. **Camada de Domínio / Serviços (Core):**
   - **`DiskManager`:** Módulo multiplataforma responsável por descobrir e filtrar exclusivamente unidades de armazenamento removíveis (`rmmovable`/SD cards), aplicando filtros defensivos contra unidades do sistema operacional local.
   - **`Raspberry Pi Imager`:** Interface oficial `--cli` para catálogo, download, escrita e verificação da imagem em unidade removível.
   - **`FirstRunBuilder`:** Gera um script Jinja2 e payload com o perfil Totem e os entrega ao Imager via `--first-run-script`, sem modificar manualmente a imagem na partição FAT.
   - **`SshManager`:** Cliente SSH assíncrono (usando `paramiko` ou `asyncssh`) para inspeção de configurações ativas, criação de backups e substituição atômica de parâmetros remotos.
3. **Camada de Templates:** Modelos Jinja2 para geração declarativa e idempotente dos arquivos de configuração do Totem (`lightdm.conf`, `autostart`, `firstboot.sh`).

---

## 2. Componentes e Arquivos Afetados

```text
totem_cli/
├── pyproject.toml                     # Adição de dependências: jinja2, paramiko
├── specs/
│   └── totem-provisioning/
│       ├── requirements.md            # Aprovado na Fase 1
│       └── plan.md                    # Este documento
├── totem_cli/
│   ├── app.py                         # Orquestração do Textual, integração com Core
│   ├── core/
│   │   ├── __init__.py
│   │   ├── disk.py                    # Detecção e segurança de drives (Linux + Windows)
│   │   ├── imager.py                  # Catálogo Bookworm e integração com Raspberry Pi Imager CLI
│   │   ├── injector.py                # Geração do script e payload do primeiro boot
│   │   └── ssh.py                     # Cliente de inspeção e edição remota via SSH
│   └── templates/
│       ├── userconf.j2                # userconf.txt para Raspberry Pi OS
│       ├── config_rpi5.j2             # Parâmetros de vídeo KMS para config.txt
│       ├── autostart.j2               # Openbox autostart (Kiosk Chromium + Onboard + xrandr)
│       └── firstboot.sh.j2            # Script de inicialização automática no primeiro boot
```

---

## 3. Decisões Técnicas e Justificativas

### 3.1. Detecção de Discos Segura (Proteção contra Destruição de Dados)
- **Linux:** Utilizar `lsblk -J -b -o NAME,SIZE,TYPE,TRAN,RM,HOTPLUG,MOUNTPOINTS,MODEL`. O sistema rejeitará qualquer dispositivo que tenha `RM=false` (não removível) ou que possua mountpoint `/`, `/boot` ou `/home`.
- **Windows:** Utilizar consulta WMI via PowerShell (`Get-CimInstance Win32_DiskDrive | Where-Object { $_.InterfaceType -eq 'USB' }`) e `Get-Partition`. Drives contendo o volume `C:` ou partições de sistema são expressamente excluídos.
- **Justificativa:** Previne que o operador selecione e destrua seu próprio sistema operacional ou unidades secundárias de trabalho.

### 3.2. Gravação via Raspberry Pi Imager CLI
- **Estratégia:** Resolver a imagem Bookworm no catálogo oficial e chamar `rpi-imager --cli` com URI, checksum, dispositivo removível e `--first-run-script`.
- **Justificativa:** O Imager faz download, gravação e verificação e oferece o mecanismo de customização usado pelo Raspberry Pi OS Bookworm.

### 3.3. Injeção de Primeiro Boot (Provisionamento Kiosk)
- **Estratégia:**
  1. O Imager grava um script de primeiro boot no formato `systemd`, compatível com Bookworm.
  2. O script cria a conta do Totem, aplica hostname e fuso horário e configura o Wi-Fi com NetworkManager quando solicitado.
  3. O script instala o perfil X11/LightDM/Openbox/Chromium e aplica as configurações no sistema de arquivos root:
     - Configuração do LightDM para autologin.
     - Criação de `/etc/xdg/openbox/autostart` com rotação vertical de tela, Onboard e Chromium em modo Kiosk apontando para a URL do Nexus.
- **Justificativa:** Garante que o cartão já suba 100% operacional no chão de fábrica sem depender de periféricos adicionais.

### 3.4. Auditoria e Modificação Remota via SSH
- **Estratégia:** Biblioteca `paramiko` para execução remota de comandos encapsulados:
  - Leitura: Consulta aos arquivos `/etc/lightdm/lightdm.conf` e `/etc/xdg/openbox/autostart`.
  - Escrita: Criação de backup `.bak-<timestamp>`, escrita via arquivo temporário em `/tmp/` e movimentação atômica via `sudo mv`.
- **Justificativa:** Garante transações atômicas — se a gravação falhar no meio, o arquivo de produção anterior permanece intacto.

---

## 4. Riscos Técnicos e Pontos de Atenção

| Risco | Impacto | Mitigação |
|---|---|---|
| Permissão insuficiente no host | Falha ao tentar abrir o drive de bloco para escrita | A CLI deve checar no startup se possui privilégios de `root` (Linux) ou Administrador (Windows) e instruir o usuário claramente caso não possua. |
| Cartão removido durante a gravação | Corrupção do microSD e exceção não tratada | Capturar `IOError`/`OSError`, emitir alerta visual na TUI e invalidar o processo com limpeza de buffers. |
| Queda de energia no primeiro boot | Sistema inacabado | O script de `firstboot.sh` deve criar uma flag de conclusão (`/etc/totem-provisioned`) apenas na última linha e auto-remover sua invocação. |
| Conexão SSH interrompida durante edição | Possível arquivo truncado | Gravar sempre em arquivo temporário `/tmp/...` e usar `mv` atômico; nunca usar operadores de redirecionamento `>` direto no arquivo alvo. |

---

## 5. Ordem de Dependência na Implementação

1. **Dependências do projeto (`pyproject.toml`):** Adicionar `jinja2`, `paramiko` e bibliotecas de criptografia.
2. **Módulo de Disco (`core/disk.py`):** Detecção, listagem segura e filtros de proteção.
3. **Módulo de Gravação (`core/flasher.py`):** Escrita em bloco com streaming `.xz` e barra de progresso.
4. **Módulo de Injeção (`core/injector.py`):** Manipulação da partição FAT32 e templates Jinja2.
5. **Módulo SSH (`core/ssh.py`):** Conexão, leitura de estado e patch atômico.
6. **Integração na TUI (`totem_cli/app.py`):** Conexão das telas do Textual aos métodos assíncronos do Core.
