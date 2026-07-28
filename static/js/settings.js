// ── Modal de configuración ──
function openSettingsModal() { document.getElementById('settings-modal').classList.remove('hidden'); }
function closeSettingsModal() { document.getElementById('settings-modal').classList.add('hidden'); }

// ── Tooltips de ayuda junto a cada campo del modal ──
function toggleTip(tipId) {
    document.getElementById('tip-' + tipId).classList.toggle('hidden');
}
