# Контракт API

Форма ответов закреплена тестом `bot/test_api_contract.py` (настоящее приложение, точный набор ключей и типы).
Меняя ответ, обновляйте вместе: сервер, этот файл, тест и клиент (`script.js`). Значения ниже заглушки,
реальных данных нет.

## Общее

- Адрес: `API_URL` в `script.js`. Заголовок каждого запроса: `Authorization: tma <initData>` (подписанная строка Telegram).
- Тело POST: JSON-объект с **точным** набором ключей (лишние или недостающие ключи дают 400).
- `request_id`: строка из `A-Z a-z 0-9 -`, 8-64 символа. Новый на каждое действие пользователя, тот же на повторы действия.
- Типы: `int` целое число (JSON number), `str` строка, `bool` true/false, `[int]` список, `T|null` значение или null.
  «всегда» значит, что ключ есть в каждом успешном ответе; «только если ...» значит, что ключа может не быть.

### Общие ошибки
| Код | Тело | Когда |
|---|---|---|
| 401 | `{"detail": "Unauthorized"}` | нет или неверная подпись initData (одно и то же при любой причине) |
| 429 | `{"error": "too_many_requests"}` + заголовок `Retry-After` (целые секунды, не меньше 1) | превышена частота; проверка идёт после проверки подписи. Группа «write»: все POST, «read»: все GET |
| 400 | зависит от эндпоинта (ниже) | неверная форма тела |
| 409 | `{"detail": "<код>"}` (у `level_locked` ещё `required_level`) | правило игры не позволяет действие |

Клиент повторяет POST (тем же `request_id`) только при сетевой ошибке, таймауте, 429 и 5xx; ответы 2xx и остальные 4xx не повторяются.

## GET /api/me
Группа read. 200:

| Поле | Тип | Наличие |
|---|---|---|
| balance | int | всегда |
| rate | int | всегда (фишек в час) |
| seconds_to_next | int | всегда |
| level | int | всегда (уровень профиля, считается по накопленному опыту) |
| income_level | int | всегда |
| storage_level | int | всегда |

Побочные эффекты: закрывает просроченную игру в мины игрока, начисляет фишки по часам.

## POST /api/roulette/spin
Группа write. Тело: `{"request_id": str, "bets": [{"type": str, "value": int|null, "amount": int}]}`.
200 (`replayed: true` при повторе того же `request_id`; тогда `balance` текущий):

| Поле | Тип | Наличие |
|---|---|---|
| number | int | всегда (0..36) |
| stake_total | int | всегда |
| payout_total | int | всегда |
| net | int | всегда (payout_total - stake_total) |
| balance | int | всегда |
| replayed | bool | всегда |

Ошибки: 400 `{"detail": "invalid_bets"}`; 409 `{"detail": "insufficient_funds"}` или `{"detail": "balance_limit"}`.

## GET /api/chat/top
Группа read. Вне группового чата (личная переписка) 200 `{"scope": "none"}`: **других полей нет**.
В беседе 200:

| Поле | Тип | Наличие |
|---|---|---|
| scope | str | всегда (`"chat"`) |
| top | [объект] | всегда, до 10 элементов |
| me | объект | всегда |
| chat_staked | int | всегда |

Элемент `top`: `rank int`, `name str`, `balance int`, `is_me bool`, `staked int`, `level int` (все всегда).
`me`: `rank int`, `balance int`, `total int` (число участников рейтинга), `staked int`, `level int` (все всегда).

## GET /api/farm
Группа read. 200 (все ключи всегда):

| Поле | Тип |
|---|---|
| balance | int |
| profile | `{level int, xp int, staked int, next_threshold int\|null}` (level и next_threshold считаются по xp, staked это общая сумма ставок, только информация; next_threshold null на максимальном уровне) |
| slots | `{used int, total int}` |
| income | `{level int, max int, rate int, next_rate int\|null, next_cost int\|null, can_buy bool, reason str\|null}` |
| storage | `{level int, max int, hours int, next_hours int\|null, next_cost int\|null, can_buy bool, reason str\|null}` |

`reason`: `null`, `"max_level"`, `"level_locked"` или `"insufficient_funds"` (первая причина в порядке проверок покупки).
На максимальном уровне `next_*` равны null.

## POST /api/farm/buy
Группа write. Тело: `{"request_id": str, "kind": "income"|"storage"}`. 200:
`kind str`, `level_after int`, `cost int`, `balance int`, `replayed bool` (все всегда).
Ошибки: 400 `{"detail": "invalid_request"}`; 409: `{"detail": "max_level"}`, `{"detail": "level_locked", "required_level": int}`,
`{"detail": "insufficient_funds"}`.

## Мины
Раскладка мин активной игры не отдаётся никогда. Игра закрывается автоматически через 24 часа без действий.

**game** (активная игра), все ключи всегда: `bet int`, `mines int`, `revealed [int]`, `safe_left int`, `multiplier str`
(например `"1.10"`, показывать как есть), `payout_now int`, `next_multiplier str|null`, `next_payout int|null`
(null только если безопасных клеток не осталось, у активной игры такого не бывает), `expires_at int` (unix).

**last** (завершённая игра), все ключи всегда: `status str` (`lost`, `cashed`, `auto_cashed`, `refunded`, `auto_refunded`),
`bet int`, `mines int`, `revealed [int]`, `mine_cells [int]` (раскладка, есть только здесь), `payout int`, `finished_at int`.

Ошибки всех POST мин: 400 `{"detail": "invalid_request"}` (неверные типы, диапазоны, ключи, `request_id`);
409 `{"detail": "<код>"}`: `active_game_exists`, `insufficient_funds`, `no_active_game`, `already_revealed`,
`request_conflict` (тот же `request_id` с другими параметрами или действием).
Повтор с тем же `request_id` и теми же параметрами возвращает **сохранённый** ответ с `replayed: true`
(`balance` в нём тот, что был при первом выполнении).

### POST /api/mines/start
Группа write. Тело: `{"request_id": str, "bet": int (1..1000000000), "mines": int (1..24)}`.
200: `game` (объект game), `balance int`, `replayed bool` (все всегда).

### POST /api/mines/reveal
Группа write. Тело: `{"request_id": str, "cell": int (0..24)}`. 200, три варианта по `result`:

| result | game | last | остальное |
|---|---|---|---|
| `"safe"` | объект game | **ключа нет** | `balance int`, `replayed bool` |
| `"mine"` | `null` | объект last | `balance int`, `replayed bool` |
| `"cleared"` (все безопасные клетки открыты, автовыплата) | `null` | объект last | `balance int`, `replayed bool` |

`result` и `balance`, `replayed` есть всегда. **Ключа `last` нет при `result: "safe"`** (клиент не должен ждать `last: null`).

### POST /api/mines/cashout
Группа write. Тело: `{"request_id": str}`. 200: `last` (объект last), `balance int`, `replayed bool` (все всегда).
При нуле открытых клеток возвращается ставка, `last.status` равен `"refunded"`.

### GET /api/mines/state
Группа read. 200: `game` (game или null), `last` (last или null), `balance int` (все три ключа всегда).
`last` может быть заполнен и при активной игре (это предыдущая завершённая игра). Автоматически закрытая
просроченная игра приходит в `last` со статусом `auto_cashed` или `auto_refunded`.

## Кено
Поле 1..40; игрок выбирает от 1 до 10 разных чисел, сервер вытягивает 10 разных чисел. Один раунд за запрос.
Таблица множителей: `bot/keno.py` (`PAYTABLE`), клиенту отдаётся эндпоинтом ниже. Примеры ответов (из них строится мок клиента
и по ним тест проверяет форму): `docs/examples/keno.json`.

### POST /api/keno/play
Группа write. Тело: `{"request_id": str, "bet": int (1..1000000000), "picks": [int] (1..10 разных чисел 1..40)}`. 200:

| Поле | Тип | Наличие |
|---|---|---|
| bet | int | всегда |
| picks | [int] | всегда (по возрастанию) |
| draw | [int] | всегда (10 чисел по возрастанию) |
| hits | [int] | всегда (совпавшие, по возрастанию; может быть пустым) |
| hit_count | int | всегда |
| multiplier | str | всегда (`"X.YY"`, `"0.00"` если пара не платит) |
| payout | int | всегда (`bet * множитель // 100`, 0 при проигрыше) |
| balance | int | всегда |
| level | int | всегда (уровень профиля) |
| xp | int | всегда (весь накопленный опыт) |
| replayed | bool | всегда |

Повтор с тем же `request_id` и теми же `bet` и `picks` возвращает сохранённый раунд с `replayed: true`
(`balance`, `level`, `xp` текущие). Ошибки: 400 `{"detail": "invalid_request"}` (типы, диапазоны, дубли, число выбранных не 1..10,
ключи, `request_id`); 409 `{"detail": "insufficient_funds"}`, `{"detail": "request_conflict"}` (тот же `request_id` с другими
`bet` или `picks`), `{"detail": "balance_limit"}`.

### GET /api/keno/paytable
Группа read. 200: `{"paytable": {"<k>": {"<h>": "X.YY"}}}`: ключи строками, `k` число выбранных (1..10), `h` число совпадений;
только платные пары, остальные пары дают выплату 0.

## Блэкджек
6 колод, перетасовка перед каждой раздачей, действия `hit`, `stand`, `double`, без сплита, страховки и сдачи. Дилер берёт до 17 и стоит на любых 17.
Блэкджек платит 3:2 (`bet + bet*3//2`). Колода и скрытая карта дилера в ответах никогда не присутствуют. Раздача закрывается автоматически
(stand) через 24 часа без действий. Примеры ответов (по ним тест проверяет форму, из них строится мок клиента): `docs/examples/blackjack.json`.

**Общая форма ответа** (start, action и state; все ключи всегда):

| Поле | Тип | Значение |
|---|---|---|
| status | str | `"active"`, `"finished"`, `"none"` (state без игры) |
| bet | int\|null | ставка при старте (null при `none`) |
| wager | int\|null | общая ставка с удвоением (null при `none`) |
| player | `{cards [str], total int, soft bool}`\|null | `soft`: туз считается за 11 |
| dealer | `{cards [str\|null], total int}`\|null | пока игра идёт, вторая карта `null`, `total` по открытой карте |
| actions | [str] | допустимые действия; `double` только на двух картах и если хватает баланса; у завершённой пусто |
| result | str\|null | `win`, `push`, `lose`, `bust`, `dealer_bust`, `blackjack`; null пока игра идёт |
| payout | int\|null | выплата; null пока игра идёт |
| balance | int | всегда |
| level | int | уровень профиля |
| xp | int | весь накопленный опыт |
| auto | bool | раздача закрыта автоматически (24 часа без действий) |
| replayed | bool | повтор того же `request_id` (в `state` всегда false) |

Карта: строка «ранг + масть», ранг `A 2..10 J Q K`, масть `S H D C` (например `"AS"`, `"10H"`).
Выплаты: `win`, `dealer_bust` = 2 * wager; `push` = wager; `blackjack` = bet + bet*3//2; `lose`, `bust` = 0.
Ошибки POST: 400 `{"detail": "invalid_request"}`; 409 `{"detail": "<код>"}`: `active_game_exists`, `insufficient_funds`, `no_active_game`,
`invalid_action` (double не на первых двух картах), `request_conflict` (тот же `request_id` с другими параметрами). Повтор с тем же
`request_id` и теми же параметрами возвращает сохранённый ответ с `replayed: true`.

### POST /api/blackjack/start
Группа write. Тело: `{"request_id": str, "bet": int (1..1000000000)}`. 200: общая форма. Блэкджек решается сразу (`status: "finished"`).

### POST /api/blackjack/action
Группа write. Тело: `{"request_id": str, "action": "hit"|"stand"|"double"}`. 200: общая форма.

### GET /api/blackjack/state
Группа read. 200: общая форма: активная раздача, иначе последняя завершённая, иначе `status: "none"`.

## Краш
Множитель растёт по времени сервера: `floor(100 * 2 ** (t / 6000))` в сотых, не больше ×1000.00. Точка краха выбирается при старте и в ответах активного раунда
никогда не присутствует. Все проверки (cashout, state, автозакрытие, `/api/me`) используют одно эффективное время `now_ms - 150 - started_ms`.
Вывод на множителе ниже ×1.01 запрещён (`too_early`, раунд остаётся активным). Брошенный раунд (дольше 70 секунд) и разбившийся по времени закрываются как «игрок не вывел».
Примеры ответов (по ним тест проверяет форму, из них строится мок клиента): `docs/examples/crash.json`.

**Общая форма ответа** (start, cashout и state; все ключи всегда):

| Поле | Тип | Значение |
|---|---|---|
| status | str | `"active"`, `"finished"`, `"none"` (state без игры) |
| mode | str\|null | `"auto"` или `"manual"` (null при `none`) |
| bet | int\|null | ставка |
| target | str\|null | цель авто-вывода «X.YY»; null в ручном режиме |
| elapsed_ms | int\|null | для active: эффективное время от старта (не меньше 0); иначе null |
| doubling_ms | int | время удвоения множителя (6000) |
| cap | str | предел множителя («1000.00») |
| crash_multiplier | str\|null | точка краха «X.YY», только когда раунд завершён |
| result | str\|null | `win`, `lose`; null пока идёт |
| multiplier | str\|null | множитель выплаты «X.YY» («0.00» при проигрыше); null пока идёт |
| payout | int\|null | выплата; null пока идёт |
| balance | int | всегда |
| level | int | уровень профиля |
| xp | int | весь накопленный опыт |
| auto | bool | раунд закрыт автоматически (разбился по времени без вашего вывода или брошен) |
| replayed | bool | повтор того же `request_id` (в `state` всегда false) |

Выплата: `bet * c // 100` (c в сотых). Вывод ровно на множителе краха выигрывает. Ошибки POST: 400 `{"detail": "invalid_request"}`; 409 `{"detail": "<код>"}`:
`active_game_exists`, `insufficient_funds`, `no_active_game`, `too_early`, `request_conflict`, `balance_limit`.

### POST /api/crash/start
Группа write. Тело: `{"request_id": str, "bet": int (1..1000000000), "target_x100": int (101..100000, необязательно)}`.
Без `target_x100` раунд ручной (активный), с ним авто: решается сразу (`status: "finished"`). 200: общая форма.

### POST /api/crash/cashout
Группа write. Тело: `{"request_id": str}`. 200: общая форма. Если раунд к этому моменту уже разбился, приходит итог раунда (`result: "lose"`).

### GET /api/crash/state
Группа read. 200: общая форма: активный раунд, иначе последний завершённый, иначе `status: "none"`.

