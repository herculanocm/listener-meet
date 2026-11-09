@echo off
setlocal EnableExtensions
rem gerar-exe.bat: o listener-meet.exe em pasta e o zip para compartilhar (F5.8, D-081), com dois cliques ou pelo terminal.
rem So embrulha o que o README manda, com o Python do venv: as ferramentas de build pelo lock e scripts\build_exe.py --zip.
rem A logica do build mora em scripts\build_exe.py; aqui nao entra regra nova. Sem acentos de proposito: o cmd le este
rem arquivo na pagina de codigo do console (cp850), nao em UTF-8.
rem Saida: build\exe\dist\listener-meet\listener-meet.exe e build\exe\listener-meet-VERSAO-win64.zip (~7 min, sem rede).
rem Recusas, do build_exe.py: mudanca sem commit (o zip corresponde a um commit) e o app ou um worker aberto.

title listener-meet: gerar o .exe e o zip
cd /d "%~dp0" || exit /b 1
set "PY=.venv\Scripts\python.exe"
set "CODE=0"
set "VERSION="

if not exist "%PY%" (
    echo gerar-exe: nao achei o venv em .venv\Scripts\python.exe. Crie-o como o README manda:
    echo     py -3.12 -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements\windows.txt
    echo     .venv\Scripts\python.exe -m pip install -e . --no-deps
    set "CODE=2"
    goto :fim
)

for /f "delims=" %%v in ('%PY% -c "from listener import __version__; print(__version__)"') do set "VERSION=%%v"
echo gerar-exe: listener-meet %VERSION%, em pasta e no zip para compartilhar
echo gerar-exe: [1/2] as ferramentas de build pelo lock ^(requirements\build-windows.txt^)
"%PY%" -m pip install -q --disable-pip-version-check -r requirements\build-windows.txt
set "CODE=%errorlevel%"
if not "%CODE%"=="0" (
    echo gerar-exe: a instalacao das ferramentas de build falhou ^(codigo %CODE%^)
    goto :fim
)

echo gerar-exe: [2/2] scripts\build_exe.py --zip ^(~7 min, sem rede; o app precisa estar fechado^)
"%PY%" scripts\build_exe.py --zip
set "CODE=%errorlevel%"
if not "%CODE%"=="0" (
    echo gerar-exe: o build nao saiu ^(codigo %CODE%^): o motivo esta na linha "build_exe:" acima
    goto :fim
)
echo.
echo gerar-exe: pronto
echo     o app: build\exe\dist\listener-meet\listener-meet.exe
echo     o zip: build\exe\listener-meet-%VERSION%-win64.zip  ^(compartilhe o zip inteiro: o .exe sozinho nao roda^)

:fim
rem a pausa segura a janela com o resultado (dois cliques, PowerShell); com a entrada redirecionada, ela nao espera
pause
exit /b %CODE%
