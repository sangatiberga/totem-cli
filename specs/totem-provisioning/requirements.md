# Especificação de Requisitos: Totem CLI (Provisionamento de Totens Raspberry Pi 5)

## 1. Contexto

A empresa opera Totens equipados com Raspberry Pi 5 no chão de fábrica, responsáveis pela execução da aplicação web de apontamento "Nexus" em modo Kiosk fullscreen. Atualmente, a preparação de novos equipamentos e a manutenção dos existentes dependem de um fluxo manual propenso a erros operacionais:
- Gravação manual de cartões microSD pelo Raspberry Pi Imager.
- Alteração manual de arquivos de configuração do sistema operacional via terminal (`lightdm.conf`, `autostart`, `config.txt`, scripts de rotação e teclado virtual Onboard).
- Processo complexo de renomeação de usuário, hostname e caminhos absolutos legados ao replicar imagens homologadas.

A solução consiste em uma CLI multiplataforma (Linux e Windows) com interface TUI (Terminal User Interface) estilizada e guiada em etapas (wizard), capaz de:
1. Gravar e injetar configurações de primeiro boot no microSD a partir de imagens oficiais ou customizadas da empresa.
2. Inspecionar e atualizar remotamente configurações de Totens já em operação via SSH de forma idempotente e segura.

---

## 2. User Stories

### US01 — Preparação e Gravação de Cartão SD
**Como** operador ou técnico de automação,  
**quero** selecionar a imagem do sistema operacional (Raspberry Pi OS Bookworm de 64 bits, Desktop ou Lite, ou `.img`/`.img.xz` homologada da empresa), escolher com segurança a unidade de cartão microSD e definir as variáveis do Totem (hostname, credenciais de usuário, URL do Nexus, fuso horário e Wi-Fi),
**para que** o cartão seja gravado e já inicialize com todas as configurações prontas sem intervenção pós-boot manual.

### US02 — Proteção contra Gravação Acidental
**Como** operador executando a CLI em seu computador de trabalho,  
**quero** que a ferramenta identifique e liste unicamente unidades de armazenamento removíveis/cartões SD e exija confirmação explícita antes de qualquer gravação,  
**para que** discos rígidos do sistema operacional local e dados corporativos nunca sejam sobrescritos acidentalmente.

### US03 — Injeção de Configuração de Primeiro Boot (Offline)
**Como** sistema de provisionamento,  
**quero** gravar os parâmetros de configuração na partição de boot FAT32 do cartão logo após a escrita da imagem,  
**para que** o Raspberry Pi 5 configure automaticamente rede, credenciais, ambiente gráfico (Openbox/LightDM) e Chromium em kiosk ao ligar pela primeira vez.

### US04 — Auditoria e Inspeção Remota via SSH
**Como** administrador de sistemas,  
**quero** conectar via SSH a um Totem já existente na rede corporativa e visualizar o inventário atual de configurações (hostname, usuário ativo, autologin, URL do Nexus e rotação de tela),  
**para que** eu possa diagnosticar o estado daquele equipamento sem precisar acessar fisicamente o chão de fábrica.

### US05 — Atualização Remota de Parâmetros
**Como** administrador de sistemas,  
**quero** alterar parâmetros operacionais (como trocar a URL do Nexus ou credenciais de rede) via SSH com validação prévia e reinício seguro dos serviços gráficos,  
**para que** correções e apontamentos de ambiente sejam aplicados rapidamente em lotes ou unidades pontuais sem downtime desnecessário.

---

## 3. Acceptance Criteria

### CA01 — Seleção e Detecção de Dispositivos de Armazenamento
- **QUANDO** o operador estiver no fluxo de gravação de cartão microSD,
- **ENTÃO** o sistema DEVE listar apenas dispositivos identificados como removíveis (SD Card / leitor USB).
- **QUANDO** houver qualquer disco montado como raiz (`/`) no Linux ou volume de sistema (`C:`) no Windows,
- **ENTÃO** o sistema DEVE ocultá-lo da lista e proibir terminantemente sua seleção.

### CA02 — Confirmação de Gravação
- **QUANDO** o operador selecionar a unidade de destino e avançar para a tela de revisão,
- **ENTÃO** o sistema DEVE exibir o modelo do disco, tamanho em GB, caminho do drive e exigir uma confirmação explícita (ex: digitar confirmação ou clique explícito com aviso em destaque) antes de iniciar qualquer operação de escrita em bloco.

### CA03 — Gravação e Descompressão
- **QUANDO** for fornecido um arquivo de imagem compactado em `.xz` (ex.: `Totem_Usinagem_RPi5.img.xz`),
- **ENTÃO** o sistema DEVE realizar o streaming da descompressão e gravação com barra de progresso em tempo real e verificação de integridade pós-gravação.

### CA04 — Injeção de Parâmetros de Kiosk do Totem
- **QUANDO** o cartão terminar a gravação de blocos,
- **ENTÃO** o sistema DEVE montar a partição FAT32 de boot e injetar:
  1. Script de primeiro boot oficial do Imager, com criação do usuário e senha armazenada como hash SHA-512.
  2. Parâmetros conservadores de vídeo e hardware no `/boot/firmware/config.txt`.
  3. O script de primeiro boot instala e configura:
     - Autologin no LightDM (`/etc/lightdm/lightdm.conf`).
     - Sessão Openbox com autostart configurado para rotação de display (`xrandr`), supressão de proteção de tela (`xset`), teclado Onboard e execução do Chromium em modo kiosk fullscreen apontando para a URL definida do Nexus.
     - Extensões e supressão de diálogos de erro de sessão do Chromium.

### CA05 — Conexão e Leitura SSH
- **QUANDO** o operador fornecer IP/hostname, usuário e chave privada ou senha na tela de SSH,
- **ENTÃO** o sistema DEVE estabelecer conexão segura, verificar o fingerprint do host e extrair os dados atuais:
  - Hostname do sistema.
  - Usuário de autologin no LightDM.
  - Linha de comando do Chromium no arquivo de autostart do Openbox (extraindo a URL atual do Nexus).
  - Status do serviço LightDM e do processo Chromium.

### CA06 — Atualização Segura Remota
- **QUANDO** o operador solicitar a alteração da URL do Nexus ou de flags gráficas via SSH,
- **ENTÃO** o sistema DEVE:
  1. Criar backup com timestamp do arquivo alterado (ex.: `autostart.bak-<timestamp>`).
  2. Substituir o valor de forma idempotente e atômica.
  3. Notificar o operador e solicitar confirmação antes de reiniciar o serviço gráfico ou reiniciar o Raspberry Pi.

---

## 4. Fora de Escopo

- Suporte a arquiteturas ou dispositivos legados anteriores ao Raspberry Pi 5 (ex.: Raspberry Pi 4, IHM Industrial Shields ou Raspberry Pi OS 32-bit).
- Build ou compilação local de aplicações Flutter (o escopo do Totem é Chromium Kiosk exibindo a aplicação web Nexus).
- Criação e particionamento de sistemas operacionais arbitrários diferentes de distribuições homologadas baseadas em Debian/Raspberry Pi OS.
- Envio de telemetria ou logs para servidores externos fora da rede corporativa local.

---

## 5. Restrições Conhecidas e Dependências

- **Compatibilidade do Host:** A CLI deve funcionar nativamente em Linux e Windows (PowerShell/CMD) com terminal ANSI e suporte à biblioteca Textual.
- **Permissões Administrativas:**
  - No Linux, a gravação em bloco no cartão microSD requer privilégios de `sudo` / `root`.
  - No Windows, a escrita em drives brutos requer execução com privilégios de Administrador.
- **Sistema homologado:** Produção utiliza Raspberry Pi OS Bookworm no Raspberry Pi 5. No catálogo atual do Raspberry Pi Imager, Bookworm aparece como Raspberry Pi OS (Legacy, 64-bit); a variante Lite é a base preferencial para validar o Totem dedicado.
- **Base Gráfica Homologada:** A imagem de produção atual utiliza X11, LightDM, Openbox e driver KMS (`vc4-kms-v3d`). A CLI não deve configurar Wayland/labwc.
- **Wi-Fi em Bookworm:** `wpa_supplicant.conf` na partição de boot não configura a rede nessa versão. O provisionador usa NetworkManager e precisa ser validado em hardware.
- **Pacotes gráficos:** A imagem Bookworm Lite instala X11, LightDM, Openbox, Chromium e utilitários durante o primeiro boot; o Pi precisa de acesso à rede para baixar os pacotes.
- **Rede Industrial:** Totens operam em rede fabril com IPs estáticos ou faixas restritas DHCP; a comunicação SSH e o acesso à URL do Nexus dependem de conectividade com a rede corporativa.
