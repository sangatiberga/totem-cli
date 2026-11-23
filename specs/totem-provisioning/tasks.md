# Lista de Tarefas: Totem CLI (Fase 3 — tasks.md)

## Fase 1: Fundação e Dependências
- [x] Tarefa 1.1 — Atualizar `pyproject.toml` com as dependências `jinja2`, `paramiko` e `cryptography`, e validar instalação no ambiente virtual.
- [x] Tarefa 1.2 — Criar a estrutura de diretórios `totem_cli/core/` e `totem_cli/templates/` com seus respectivos `__init__.py`.

## Fase 2: Módulo de Disco e Segurança de Armazenamento
- [x] Tarefa 2.1 — Implementar `core/disk.py`: detecção e filtragem rigorosa de drives removíveis/SD Cards para Linux via `lsblk` e Windows via WMI/PowerShell.
- [x] Tarefa 2.2 — Criar testes unitários para `core/disk.py` validando o bloqueio de partições do sistema (`/`, `C:`) e unidades não removíveis.

## Fase 3: Módulo de Gravação (Flasher) com Streaming e Verificação
- [x] Tarefa 3.1 — Implementar `core/flasher.py`: streaming de descompressão `.xz` e gravação direta em blocos de 4MB com callback de progresso e verificação SHA-256.
- [x] Tarefa 3.2 — Criar testes unitários para `core/flasher.py` simulando escrita em arquivo de imagem mock.

## Fase 4: Templates Declarativos e Injeção de Primeiro Boot
- [x] Tarefa 4.1 — Criar os templates Jinja2 (`userconf.j2`, `config_rpi5.j2`, `autostart.j2`, `firstboot.sh.j2`, `monitor_network.py.j2`, `offline.html.j2`, `nexus_monitor_service.j2`, `sudoers_totem.j2`).
- [x] Tarefa 4.2 — Implementar `core/injector.py`: renderização dos templates, URL parser (app_host/app_port), geração de hash SHA-512 e injeção na partição FAT32 de boot.
- [x] Tarefa 4.3 — Criar testes unitários para `core/injector.py` validando todos os arquivos gerados, incluindo monitor_network.py v2.0.0, offline.html, nexus-monitor.service e sudoers.

## Fase 5: Módulo de Gerenciamento Remoto via SSH
- [x] Tarefa 5.1 — Implementar `core/ssh.py`: cliente SSH com leitura de ONLINE_URL/APP_HOST/APP_PORT, atualização atômica dos três campos com backup, e restart de nexus-monitor.service + lightdm.
- [x] Tarefa 5.2 — Criar testes unitários com mocks para `core/ssh.py` cobrindo inspect_totem, update_nexus_url e restart_graphics.

## Fase 6: Integração com a Interface TUI (Textual)
- [x] Tarefa 6.1 — Integrar a seleção de drives do `core/disk.py` e tela de confirmação de segurança na tela de gravação da TUI.
- [x] Tarefa 6.2 — Integrar barra de progresso da gravação e injeção de boot na TUI.
- [x] Tarefa 6.3 — Integrar tela de auditoria e edição de parâmetros remotos via `core/ssh.py` na TUI (exibindo nexus_url, app_host, app_port, monitor_service_active).
- [x] Tarefa 6.4 — Executar smoke test completo de navegação e validação visual de toda a aplicação.

## Fase 7: Script de Produção monitor_network.py v2.0.0 e offline.html
- [x] Tarefa 7.1 — Substituir `monitor_network.py.j2` pelo script completo de produção (522 linhas) parametrizando USERNAME, HOME, ONLINE_URL, APP_HOST, APP_PORT.
- [x] Tarefa 7.2 — Criar `offline.html.j2` a partir de `/home/wesleyximenes/Downloads/offline.html` (página offline Nexus com API /api/status, /api/retry, /api/reboot).
- [x] Tarefa 7.3 — Criar `nexus_monitor_service.j2` (systemd unit para nexus-monitor.service).
- [x] Tarefa 7.4 — Criar `sudoers_totem.j2` (permissão nmcli + reboot sem senha para o monitor).
- [x] Tarefa 7.5 — Atualizar `firstboot.sh.j2` para instalar offline.html, nexus-monitor.service e sudoers, e habilitar o serviço com `systemctl enable`.
- [x] Tarefa 7.6 — Atualizar `injector.py` com `parse_nexus_url()`, novos arquivos (offline.html, service, sudoers) e variáveis corretas do monitor.
- [x] Tarefa 7.7 — Atualizar `ssh.py`: inspect_totem lê ONLINE_URL/APP_HOST/APP_PORT; update_nexus_url atualiza os três campos; restart_graphics reinicia nexus-monitor + lightdm.
- [x] Tarefa 7.8 — Atualizar todos os testes para cobrir os novos comportamentos (14 testes passando).

## Fase 8: Edição remota completa via SSH
- [x] Tarefa 8.1 — Estender `core/ssh.py`: inspeção de timezone, Wi-Fi e marcador de provisionamento; edição de hostname, timezone, rotação, senha, Wi-Fi (liga/desliga/configura), leitura de logs e upload de offline.html/splash com allowlist.
- [x] Tarefa 8.2 — Ampliar a tela de inventário SSH na TUI com seções de identidade, Nexus/tela, Wi-Fi, logs e envio de arquivos, com releitura após cada alteração.
- [x] Tarefa 8.3 — Cobrir os novos comportamentos com testes unitários com mocks em `tests/test_ssh.py`.

## Fase 9: Lote de alterações, trava Wi-Fi e ações fixas
- [x] Tarefa 9.1 — Trava `nexus-wifi-disabled` no monitor + patch remoto do monitor antigo; `set_wifi_enabled(False)` com erro dedicado quando a sessão cai junto.
- [x] Tarefa 9.2 — Lote de alterações pendentes com confirmação, passos visíveis por ação e reboot único ao final; botão Atualizar Status.
- [x] Tarefa 9.3 — Loading nos botões de ação SSH e barra de ações fixa fora da rolagem.

## Fase 10: Edição detectada, grid vertical e logs com rolagem
- [x] Tarefa 10.1 — setxkbmap br/abnt2 no autostart e locale pt_BR.UTF-8 no firstboot.
- [x] Tarefa 10.2 — Botões "Editar" por cartão; Aplicar só habilita com alteração detectada e diferente do valor atual.
- [x] Tarefa 10.3 — Cartões em grid 2 colunas (flex/grid) lado a lado e barra de ações fixa.
- [x] Tarefa 10.4 — Log em TextArea com numeração e rolagem.
