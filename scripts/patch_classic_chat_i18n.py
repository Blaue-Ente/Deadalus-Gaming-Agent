#!/usr/bin/env python3
"""One-off: replace known Cyrillic user-facing strings in ui/static/classic_chat.js with t('classic.*')."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "ui" / "static" / "classic_chat.js"

# (old_fragment, new_fragment) — exact substrings
REPLACEMENTS: list[tuple[str, str]] = [
    ("showToast('Минимум 1 таб', 1500)", "showToast(t('classic.toastMinOneTab'), 1500)"),
    (
        "confirm('Сигурен ли си? Това ще маркира стъпката като успешна без проверка на чекпойнтите.')",
        "confirm(t('classic.confirmOverrideStep'))",
    ),
    ("showToast('🌙 Стартирана фонова задача — виж Outbox', 3500)", "showToast(t('classic.toastBgTaskStarted'), 3500)"),
    (
        "if (!confirm(`Изтрий завинаги:\\n\"${title || discoveryId}\"?\\n\\nТова действие е необратимо.`)) return;",
        "if (!confirm(t('classic.confirmDeleteDiscovery', { title: title || discoveryId }))) return;",
    ),
    ("showToast('🗑 Discovery изтрита')", "showToast(t('classic.toastDiscoveryDeleted'))"),
    ("showToast('⚠ Грешка при изтриване', 2000)", "showToast(t('classic.toastDeleteError'), 2000)"),
    ("showToast('⚠ Discovery не е намерена — опресни', 2000)", "showToast(t('classic.toastDiscoveryNotFound'), 2000)"),
    (
        "showToast('▶ Discovery заредена в чата — натисни Изпрати', 3000)",
        "showToast(t('classic.toastDiscoveryLoadedChat'), 3000)",
    ),
    ("showToast('⚠ Модалът не е намерен', 2000)", "showToast(t('classic.toastModalNotFound'), 2000)"),
    ("showToast(`▶ Стъпка ${step.num} заредена в чата`, 3000)", "showToast(t('classic.toastStepLoadedChat', { n: step.num }), 3000)"),
    ("showToast('⚠ Няма зареден план', 2000)", "showToast(t('classic.toastNoPlanLoaded'), 2000)"),
    (
        "if (!confirm(`Изтрий плана:\\n\"${title}\"?\\n\\nТова действие е необратимо.`)) return;",
        "if (!confirm(t('classic.confirmDeletePlan', { title }))) return;",
    ),
    ("showToast('🗑 Планът е изтрит')", "showToast(t('classic.toastPlanDeleted'))"),
    ("if (!confirm('Нулирай Navigator статистиките?')) return;", "if (!confirm(t('classic.confirmResetNavigator'))) return;"),
    ("if (!confirm('Изтрий този episodic fact?')) return;", "if (!confirm(t('classic.confirmDeleteEpisodic'))) return;"),
    (
        "if (!confirm('Отхвърли proposal ' + pid + '?')) return;",
        "if (!confirm(t('classic.confirmRejectProposal', { pid }))) return;",
    ),
    ("if (j.ok) showToast('✅ Kernel конфиг запазен')", "if (j.ok) showToast(t('classic.toastKernelSaved'))"),
    ("showToast('❌ ' + (j.error || 'грешка'))", "showToast('❌ ' + (j.error || t('classic.errorGeneric')))"),
    ("if (!confirm('Прилагам bundle-а към проекта?')) return;", "if (!confirm(t('classic.confirmApplyBundle'))) return;"),
    (
        "if (j.ok) { showToast('✅ Bundle приложен: ' + (j.applied||[]).length + ' файла'); loadBundlesPanel(); }",
        "if (j.ok) { showToast(t('classic.toastBundleApplied', { n: (j.applied||[]).length })); loadBundlesPanel(); }",
    ),
    ("else showToast('❌ ' + (j.error||'Грешка'), 5000)", "else showToast('❌ ' + (j.error||t('classic.errorGeneric')), 5000)"),
    ("showToast('⚠ Задачата не е намерена — опресни панела', 2000)", "showToast(t('classic.toastTaskNotFoundPanel'), 2000)"),
    (
        "showToast(`▶ Задача заредена в чата — натисни Изпрати`, 3000)",
        "showToast(t('classic.toastTaskLoadedChat'), 3000)",
    ),
    ('if (!confirm(`Изтрий задача: "${title}"?`)) return;', "if (!confirm(t('classic.confirmDeleteTask', { title }))) return;"),
    ("showToast('🗑 Задачата е изтрита')", "showToast(t('classic.toastTaskDeleted'))"),
    ("if (!taskText) { showToast('Въведи текст на задачата', 2000); return; }", "if (!taskText) { showToast(t('classic.toastEnterTaskText'), 2000); return; }"),
    (
        "showToast(`⚙ Фонова задача стартирана${permLabel}`, 4000)",
        "showToast(t('classic.toastBgTaskStartedPerm', { perm: permLabel }), 4000)",
    ),
    ("if (!title) { showToast('Въведи заглавие', 2000); return; }", "if (!title) { showToast(t('classic.toastEnterTitle'), 2000); return; }"),
    (
        "showToast(`💾 Задача запазена: \"${title.slice(0,40)}\"`, 4000)",
        "showToast(t('classic.toastTaskSavedTitle', { title: title.slice(0,40) }), 4000)",
    ),
    ("showToast('⚠ ' + (d.detail || d.error || 'Грешка'), 3000)", "showToast('⚠ ' + (d.detail || d.error || t('classic.errorGeneric')), 3000)"),
    (
        "showToast(status === 'done' ? '✅ Маркирано като done' : status === 'rework' ? '🔄 Маркирано като rework' : '🔵 Преотворено')",
        "showToast(status === 'done' ? t('classic.toastMarkedDone') : status === 'rework' ? t('classic.toastMarkedRework') : t('classic.toastReopened'))",
    ),
    ("showToast('📦 Няма нови ADN кандидати в Upgrade Pool', 3000)", "showToast(t('classic.toastUpgradePoolEmpty'), 3000)"),
    (
        "showToast(`📦 Стартиран scan на ${d.candidates} кандидата → виж Outbox`, 4000)",
        "showToast(t('classic.toastUpgradePoolScan', { n: d.candidates }), 4000)",
    ),
    (
        "showToast(d.ok ? '🧠 Генериране на стил урок стартирано' : ('⚠ ' + (d.error||'')), 3500)",
        "showToast(d.ok ? t('classic.toastStyleLessonStarted') : ('⚠ ' + (d.error||'')), 3500)",
    ),
    ("if (!confirm('Приложи data batch в DB?')) return;", "if (!confirm(t('classic.confirmApplyDataBatch'))) return;"),
    (
        "showToast(`✓ Приложено — вмъкнато: ${d.result?.inserted ?? '?'}`, 'success')",
        "showToast(t('classic.toastDataBatchInserted', { n: d.result?.inserted ?? '?' }), 'success')",
    ),
    ("showToast(`Грешка: ${d.error}`, 'danger')", "showToast(t('classic.toastErrorPrefix', { msg: d.error }), 'danger')"),
    (
        "showToast('⏸ Пауза заявена — Дедал ще спре след текущата итерация', 'info')",
        "showToast(t('classic.toastJournalPauseRequested'), 'info')",
    ),
    ("if (!confirm('Изтрий journal-а за тази сесия?')) return;", "if (!confirm(t('classic.confirmDeleteJournal'))) return;"),
    ("showToast('⚠ Съобщението не е намерено — опресни outbox', 2000)", "showToast(t('classic.toastOutboxMsgNotFound'), 2000)"),
    ("showToast('⚠ Няма session_id в това съобщение', 2000)", "showToast(t('classic.toastNoSessionInMessage'), 2000)"),
    (
        "showToast(`📂 Сесия ${sid.slice(0, 14)} заредена — напиши отговора`, 3000)",
        "showToast(t('classic.toastSessionLoadedReply', { id: sid.slice(0, 14) }), 3000)",
    ),
    ("if (!cls) { showToast('Въведи perm_class', 'warn'); return; }", "if (!cls) { showToast(t('classic.toastEnterPermClass'), 'warn'); return; }"),
    (
        "showToast(`✓ Дадено разрешение: ${cls} (${hours}ч)`, 'success'); loadPermissions(); document.getElementById('perm-class-input').value = ''; }",
        "showToast(t('classic.toastPermGranted', { cls, hours }), 'success'); loadPermissions(); document.getElementById('perm-class-input').value = ''; }",
    ),
    ("else showToast(`Грешка: ${d.error}`, 'danger')", "else showToast(t('classic.toastErrorPrefix', { msg: d.error }), 'danger')"),
    (
        "if (d.ok) { showToast(`✓ ${cls} — ${hours}ч`, 'success'); loadPermissions(); }",
        "if (d.ok) { showToast(t('classic.toastPermQuick', { cls, hours }), 'success'); loadPermissions(); }",
    ),
    (
        "if (!confirm(`Да разреша ВСИЧКИ ${ALL_PERM_IDS.length} известни permissions за ${hours}ч?`)) return;",
        "if (!confirm(t('classic.confirmGrantAllPerms', { count: ALL_PERM_IDS.length, hours }))) return;",
    ),
    (
        "showToast(`✓ Разрешени ${d.count || 0} permissions (${hours}ч)`, 'success')",
        "showToast(t('classic.toastPermsGrantedCount', { count: d.count || 0, hours }), 'success')",
    ),
    (
        "showToast(`↻ Подновени ${d.count} permissions (${hours}ч)`, 'success')",
        "showToast(t('classic.toastPermsRenewed', { count: d.count, hours }), 'success')",
    ),
    ("showToast('Няма изтекли — всичко е активно', 'info')", "showToast(t('classic.toastNoExpiredPerms'), 'info')"),
    ("showToast(`Разрешение отменено: ${cls}`, 'warn')", "showToast(t('classic.toastPermRevoked', { cls }), 'warn')"),
    ("showToast('⚠ Няма активна bg сесия', 2000)", "showToast(t('classic.toastNoBgSession'), 2000)"),
    ("showToast(`📂 Сесия вече отворена — превключено`, 2000)", "showToast(t('classic.toastSessionAlreadyOpen'), 2000)"),
    ("showToast(`📂 Заредена сесия: ${targetSid.slice(0, 16)}`, 2500)", "showToast(t('classic.toastSessionLoaded', { id: targetSid.slice(0, 16) }), 2500)"),
    (
        "if (!confirm('Approve всички pending code proposals наведнъж?')) return;",
        "if (!confirm(t('classic.confirmApproveAllProposals'))) return;",
    ),
    (
        "showToast(`✓ Приложени: ${d.applied}  Неуспешни: ${d.failed?.length || 0}`, 'success')",
        "showToast(t('classic.toastBulkApplyResult', { applied: d.applied, failed: d.failed?.length || 0 }), 'success')",
    ),
    ("if (!task) { showToast('Въведи задача', 'warn'); return; }", "if (!task) { showToast(t('classic.toastEnterTask'), 'warn'); return; }"),
    (
        "showToast(`▶ Стартирана фонова задача (${d.session_id})`, 'success')",
        "showToast(t('classic.toastBgTaskStartedWithId', { id: d.session_id }), 'success')",
    ),
    ("showToast(d.message || d.error || 'Няма auto-fix', 'warn')", "showToast(d.message || d.error || t('classic.toastNoAutoFix'), 'warn')"),
    (
        "showToast('Няма blockers с разпознати permissions за auto-fix', 'warn')",
        "showToast(t('classic.toastNoBlockersAutoFix'), 'warn')",
    ),
    (
        "if (!confirm('Смени playground проекта на Kind=static (npm firewall workaround)?')) return;",
        "if (!confirm(t('classic.confirmSwitchStaticKind'))) return;",
    ),
    (
        "showToast(`🔓 Grant: ${(d.granted || []).length} permissions (24ч)`, 'success')",
        "showToast(t('classic.toastGrantBatch24h', { n: (d.granted || []).length }), 'success')",
    ),
]


def main() -> None:
    s = JS.read_text(encoding="utf-8")
    for old, new in REPLACEMENTS:
        if old not in s:
            raise SystemExit(f"missing expected fragment:\n{old[:120]}")
        s = s.replace(old, new, 1)
    JS.write_text(s, encoding="utf-8")
    print("patched", JS)


if __name__ == "__main__":
    main()
