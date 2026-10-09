"""Запросы числа завершённых раундов игрока по всем играм. Нейтральный модуль: его используют рефералка (квалификация) и патина, не зависящие друг от друга."""

# Завершённые раунды игрока по всем играм (квалификация приглашённого): разовые игры завершены всегда, у партий считаются только закрытые (finished_at)
ROUND_COUNT_SQL = (
    "SELECT COUNT(*) FROM roulette_rounds WHERE telegram_id = ?",
    "SELECT COUNT(*) FROM keno_rounds WHERE telegram_id = ?",
    "SELECT COUNT(*) FROM slot_rounds WHERE telegram_id = ?",
    "SELECT COUNT(*) FROM mines_games WHERE telegram_id = ? AND finished_at IS NOT NULL",
    "SELECT COUNT(*) FROM blackjack_games WHERE telegram_id = ? AND finished_at IS NOT NULL",
    "SELECT COUNT(*) FROM crash_games WHERE telegram_id = ? AND finished_at IS NOT NULL AND COALESCE(result, '') != 'refund'",
    "SELECT COUNT(*) FROM crash_bets WHERE telegram_id = ? AND status != 'open'",      # живой краш: закрытые ставки
    "SELECT COUNT(*) FROM hilo_games WHERE telegram_id = ? AND finished_at IS NOT NULL",
)
