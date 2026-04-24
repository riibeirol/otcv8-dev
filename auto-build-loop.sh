#!/bin/bash
# Auto-build loop do OTCv8. Roda MSBuild via cmd.exe, captura erros,
# aplica fixes conhecidos via apply_fix.py, commita e tenta de novo.
# Notifica WhatsApp no fim (ok ou desistiu).
set -u

MAX_ITER=30
SRC=/mnt/c/otcv8-dev
LOGS=/tmp/auto-build
mkdir -p "$LOGS"

WA_URL="http://localhost:8081/message/sendText/personal"
WA_KEY="leo-assistant-key-2024"
WA_NUMBER="5547988542663"

# Mantém a janela aberta ao final pra Leonardo ver o resultado
_pause_on_exit() {
    echo
    echo "===================== LOOP FINALIZADO ====================="
    echo "pressione ENTER pra fechar a janela"
    read _dummy
}
trap _pause_on_exit EXIT

notify() {
    curl -s -X POST "$WA_URL" \
        -H "apikey: $WA_KEY" -H "Content-Type: application/json" \
        -d "{\"number\":\"$WA_NUMBER\",\"text\":$(jq -Rn --arg t "$1" '$t')}" >/dev/null 2>&1
    echo "[$(date +%H:%M:%S)] $1"
}

notify "🤖 Auto-build loop iniciado. Vou iterar ate compilar ou desistir."

for i in $(seq 1 $MAX_ITER); do
    LOG="$LOGS/iter-$i.log"
    notify "🔨 Iteracao $i/$MAX_ITER — rodando MSBuild..."

    # Build direto via WSL interop (chama .exe Windows como binário normal)
    cd "$SRC/vc16" || exit 1
    MSBUILD="/mnt/c/Program Files/Microsoft Visual Studio/2022/Community/MSBuild/Current/Bin/MSBuild.exe"
    echo "============================================================"
    echo "  MSBuild log: $LOG"
    echo "============================================================"
    "$MSBUILD" \
        "/property:Configuration=DirectX" \
        "/p:PlatformToolset=v143" \
        "/p:WindowsTargetPlatformVersion=10.0" \
        "/m" "/nologo" "/v:m" \
        2>&1 | tee "$LOG"
    BUILD_RC=${PIPESTATUS[0]}

    # Check sucesso: binário existe e recente
    if [ -f "$SRC/otclient_dx.exe" ] && [ "$(find "$SRC/otclient_dx.exe" -newer "$LOG" | wc -l)" -eq 0 ]; then
        AGE=$(($(date +%s) - $(stat -c%Y "$SRC/otclient_dx.exe")))
        if [ "$AGE" -lt 300 ]; then
            SIZE=$(($(stat -c%s "$SRC/otclient_dx.exe") / 1024 / 1024))
            # backup atual e copia
            cp /mnt/c/Troyale-Client/otclient_dx.exe "/mnt/c/Troyale-Client/otclient_dx.exe.pre-patch-$(date +%Y%m%d-%H%M%S)"
            cp "$SRC/otclient_dx.exe" /mnt/c/Troyale-Client/otclient_dx.exe
            notify "✅ SUCESSO — otclient_dx.exe (${SIZE}MB) patched copiado pra C:\\Troyale-Client. Abre OTCv8 e testa os tiles."
            exit 0
        fi
    fi

    # Pegou falha — aplica fixes
    notify "❌ iter $i falhou, analisando erros..."
    /home/riibeirol/otcv8-dev/apply-fix.py "$LOG" > "$LOGS/fix-$i.log" 2>&1
    FIX_RC=$?

    if [ "$FIX_RC" -eq 2 ]; then
        notify "⚠️ Sem fix conhecido pros erros da iter $i. Log: $LOG — vou pausar."
        exit 1
    fi

    if [ "$FIX_RC" -eq 3 ]; then
        notify "🔁 Mesmo erro que iter anterior. Desistindo — precisa ajuste manual. Log: $LOG"
        exit 1
    fi

    # commit + push
    cd /home/riibeirol/otcv8-dev
    # copia mudanças que foram feitas em /mnt/c/otcv8-dev pra cá (pra commitar)
    # NÃO — apply-fix.py já edita ambos lados? melhor editar só WSL e push depois deploy
    # Simplificando: apply-fix.py atua em /mnt/c/otcv8-dev (mesmo que .bat usa).
    # Mas commit/push é do nosso ~/otcv8-dev. Precisa sincronizar.
    rsync -a --delete /mnt/c/otcv8-dev/src/ /home/riibeirol/otcv8-dev/src/ 2>/dev/null
    git add src/
    if git diff --cached --quiet; then
        notify "⚠️ Fix não produziu diff commitável. Desistindo."
        exit 1
    fi
    git commit -m "Auto-fix iter $i" 2>&1 | tail -1
    git push origin master 2>&1 | tail -1
    notify "✅ iter $i — fix aplicado e committado, indo pra próxima."
    sleep 2
done

notify "⏱️ Max iter ($MAX_ITER) atingido sem sucesso. Desistindo."
exit 1
