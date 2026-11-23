<div align="center">
<pre>
 ████  ███  █   █  ███   ███  █████ ███    █████  ███  █████ █████ █   █   
█ ░░░░█ ░░█ ██  █░█ ░░░ █ ░░█  ░█░░░ █░░    ░█░░░█ ░░█  ░█░░░█░░░░░██ ██░  
 ███░░█████░█░█ █░█░ ██░█████░  █░░░░█░░░    █░░░█░ ░█░  █░░░████░░█░█ █░░ 
  ░░█ █░░░█░█░░██░█░░ █░█░░░█░░ █░░  █░░     █░░ █░░ █░░ █░░ █░░░░ █░░░█░░ 
████░░█░░░█░█░░ █░░███ ░█░░░█░░ █░░ ███░     █░░  ███ ░░ █░░ █████░█░░ █░░ 
 ░░░░ ░░░  ░░░░  ░░ ░░░ ░░░  ░░  ░░  ░░░      ░░   ░░░ ░  ░░  ░░░░░ ░░  ░░ 
  ░░░░  ░   ░ ░   ░  ░░░  ░   ░   ░   ░░░      ░    ░░░    ░   ░░░░░ ░   ░ 
</pre>
Assistente de terminal para preparar e administrar Totens baseados em Raspberry Pi 5.
</div>

## Comando `totem`

O comando da ferramenta é `totem`. Os instaladores baixam o binário compilado da última release do GitHub, verificam o checksum SHA-256 e o colocam no PATH; não é necessário Python, `pipx`, Git ou clonar o projeto. Requer apenas um token com leitura do repositório privado `sangatiberga/totem-cli` (fine-grained com Contents Read-only, ou clássico com escopo `repo`). Binários publicados: `totem-linux-amd64` e `totem-windows-amd64.exe` (Linux/Windows 64-bit).

### Linux

Instale com um comando (o token vai no cabeçalho porque o repositório é privado):

```bash
export GITHUB_TOKEN='SEU_TOKEN'; curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" https://raw.githubusercontent.com/sangatiberga/totem-cli/main/install.sh | sh
```

Alternativa sem one-liner: baixe o `install.sh` pelo navegador (logado no GitHub) e execute `GITHUB_TOKEN='SEU_TOKEN' sh install.sh`.

Depois, inicie a ferramenta com `totem`. Antes de gravar um cartão, a CLI solicitará a senha `sudo` em um campo mascarado.

### Windows

No PowerShell, instale com um comando (o token vai no cabeçalho porque o repositório é privado):

```powershell
$env:GITHUB_TOKEN='SEU_TOKEN'; irm https://raw.githubusercontent.com/sangatiberga/totem-cli/main/install.ps1 -Headers @{Authorization="Bearer $env:GITHUB_TOKEN"} | iex
```

Alternativa sem one-liner: baixe o `install.ps1` pelo navegador (logado no GitHub) e execute `powershell -ExecutionPolicy Bypass -File install.ps1` com `$env:GITHUB_TOKEN` definido.

O instalador instala `pipx` se necessário. Feche e reabra o PowerShell e inicie a ferramenta:

```powershell
totem
```

Para gravar um cartão, abra o PowerShell como Administrador e execute `totem`.

O instalador obtém a versão mais recente publicada. Ao publicar uma tag `v*`, o workflow de release anexa os binários `totem-linux-amd64` e `totem-windows-amd64.exe`, além de `.whl`, `.tar.gz` e `checksums.txt`, à release do GitHub.

Para desenvolvimento, clone o repositório e instale em modo editável com `pipx install --editable .`.

### Ambiente de desenvolvimento

Também é possível instalar sem `pipx`, usando um ambiente virtual local. No Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
totem
```

No Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install --editable .
totem
```

O fluxo de gravação usa o modo CLI oficial do Raspberry Pi Imager, consulta o catálogo oficial para resolver as imagens Bookworm e inclui o perfil Totem no script oficial de primeiro boot do Imager. Para imagem customizada, use uma imagem Raspberry Pi OS Bookworm de 64 bits compatível com o primeiro boot via systemd.

## Decisões registradas

- UI multiplataforma com Python + Textual.
- Perfis exclusivos para Totem Raspberry Pi 5.
- Base de produção: Raspberry Pi OS Bookworm de 64 bits, identificado no Raspberry Pi Imager como Raspberry Pi OS (Legacy). A variante Lite é a recomendação para o perfil dedicado Openbox + Chromium; a variante com desktop também pode ser selecionada.
- Wi-Fi opcional e Ethernet DHCP.
- SSH por senha ou chave privada; na primeira conexão, a CLI mostra a impressão digital da chave do host para o usuário comparar antes de salvar.
- O perfil gráfico de produção é X11 + LightDM + Openbox + Chromium em modo kiosk.

## Estado e limitações

- O primeiro boot cria o usuário e hostname, configura Wi-Fi por NetworkManager, instala X11/LightDM/Openbox/Chromium e aplica o autostart do Totem.
- No Linux, a CLI solicita a senha sudo antes de instalar/usar o Imager para gravação. Se a versão do repositório Ubuntu for antiga, baixa e instala o pacote `.deb` oficial atual. No Windows, abra o terminal como Administrador; se necessário, a CLI tenta instalar o Imager via winget.
- Cartões removíveis montados pelo ambiente gráfico aparecem na seleção. O Imager controla o acesso ao destino durante a gravação; confirme modelo e capacidade no diálogo antes de prosseguir.
- A opção **Erase** do assistente gráfico do Imager não existe no modo `--cli` oficial, que exige um arquivo/URL de imagem e um dispositivo de destino. Para apenas formatar/apagar sem gravar um OS, use o Imager gráfico.
- A escrita e a instalação de pacotes ainda precisam ser validadas em um cartão de teste e em um Raspberry Pi 5 Bookworm antes de produção.
- Para instalar os pacotes no primeiro boot, o Raspberry Pi precisa ter Ethernet ou Wi-Fi com acesso aos repositórios Debian/Raspberry Pi.
