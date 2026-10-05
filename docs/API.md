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
| 413 | `{"detail": "payload_too_large"}` | тело больше 64 КБ (по `Content-Length` отказ до чтения; без него чтение обрывается на пределе) |
| 503 | `{"detail": "busy"}` + `Retry-After: 1` | база занята дольше таймаута; запрос ничего не изменил, его можно повторить тем же `request_id` |
| 409 | `{"detail": "<код>"}` (у `level_locked` ещё `required_level`) | правило игры не позволяет действие |

Все ответы несут `X-Content-Type-Options: nosniff` и `Referrer-Policy: no-referrer`, ответы `/api/*` ещё `Cache-Control: no-store`.

Клиент повторяет POST (тем же `request_id`) только при сетевой ошибке, таймауте, 429 и 5xx; ответы 2xx и остальные 4xx не повторяются.

## GET /api/me
Группа read. 200:

| Поле | Тип | Наличие |
|---|---|---|
| balance | int | всегда |
| rate | int | всегда (фишек в час) |
| seconds_to_next | int | всегда: секунд до следующего минутного начисления (1..60; раньше до часового) |
| level | int | всегда (уровень профиля, считается по накопленному опыту) |
| income_level | int | всегда |
| storage_level | int | всегда |
| farm | `{income_per_hour int, per_minute_estimate str, next_tick_in_s int, hours_cap int, accrued_now int}` | всегда: поминутное начисление дохода (ниже) |
| active_game | str\|null | всегда: `"mines"`, `"blackjack"`, `"crash"`, `"hilo"` (незавершённая игра игрока; если активных несколько, та, где действие было позже) или null |
| incoming_unseen | `{count int, total int}` | всегда: непросмотренные входящие переводы (число и сумма, которую получатель получил) |
| transfer_limits | `{min int, max int, daily_left int, fee_percent int, min_level int, cooldown_seconds int, min_age_hours int, min_staked int, unlimited bool}` | всегда: лимиты переводов для клиента; у владельца `fee_percent` 0, `unlimited` true, `daily_left` равен 9007199254740991 |

Побочные эффекты: закрывает просроченные игры (мины, блэкджек, краш, хило) игрока, подтягивает поминутное начисление дохода. `active_game` считается после закрытия просроченных игр.

**Поминутное начисление дохода.** Доход в час R (`income_per_hour`, он же `rate`) приходит минутными тиками. Сервер считает их лениво: любой запрос, затрагивающий игрока (`/api/me`, ставка, ход, перевод,
покупка улучшения), сначала зачисляет все полностью прошедшие минуты; фонового задания нет. Если минута ещё не прошла, `GET /api/me` ничего не пишет в базу. У игрока хранятся метка `last_accrual` (граница минуты)
и остаток `accrual_acc` (от -59 до 0, в долях фишки * 60). Каждую минуту: `acc += R`, `credit = ceil(acc / 60)` (не меньше 0), `acc -= credit * 60`; сумма за любые 60 минут при неизменном R равна ровно R,
расхождение с точным интегралом меньше одной фишки (R=1500 даёт ровно 25 каждую минуту, R=100 даёт 2, 2, 1 по кругу). Отсутствие дольше потолка (`hours_cap` часов = 30 + 6 * уровень хранилища): платится только за потолок,
лишнее сгорает, метка переносится на текущую минуту. Часы сервера идут назад: ничего не начисляется. Баланс не выше 9007199254740991.
`per_minute_estimate` это R/60 с одним знаком («1.7»), `next_tick_in_s` секунд до следующей минутной границы (1..60, равно `seconds_to_next`), `accrued_now` сколько фишек зачислил именно этот запрос (для «+N» у баланса).
Покупка улучшения сначала зачисляет начисление по старой ставке и старому потолку, дальнейшие минуты считаются по новым. Кнопки «Забрать» для фермы нет: фишки приходят сами.

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

Ошибки: 400 `{"detail": "invalid_bets"}`; 409 `{"detail": "insufficient_funds"}`, `{"detail": "balance_limit"}` или `{"detail": "request_conflict"}` (тот же `request_id` с другим набором ставок; порядок ставок значения не имеет).

## GET /api/chat/top
Группа read. Вне группового чата (личная переписка) 200 `{"scope": "none"}`: **других полей нет**.
В беседе 200:

| Поле | Тип | Наличие |
|---|---|---|
| scope | str | всегда (`"chat"`) |
| top | [объект] | всегда, до 10 элементов |
| me | объект | всегда |
| chat_staked | int | всегда |

Элемент `top`: `rank int`, `name str`, `balance int`, `is_me bool`, `staked int`, `level int`, `member_ref str|null` (все всегда; `member_ref` непрозрачная метка участника для перевода, у самого себя null).
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
Множитель растёт по времени сервера: `floor(100 * 2 ** (t / 6000))` в сотых, не больше ×250.00. Точка краха выбирается при старте и в ответах активного раунда
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
| cap | str | предел множителя («250.00») |
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
Группа write. Тело: `{"request_id": str, "bet": int (1..1000000000), "target_x100": int (101..25000, необязательно)}`.
Без `target_x100` раунд ручной (активный), с ним авто: решается сразу (`status: "finished"`). 200: общая форма.

### POST /api/crash/cashout
Группа write. Тело: `{"request_id": str}`. 200: общая форма. Если раунд к этому моменту уже разбился, приходит итог раунда (`result: "lose"`).

### GET /api/crash/state
Группа read. 200: общая форма: активный раунд, иначе последний завершённый, иначе `status: "none"`.

## Хило
Угадайте, будет ли следующая карта выше или ниже текущей. 13 достоинств (туз = 1 младший ... король = 13), каждая карта выбирается независимо и равномерно, масти `S H D C` только для вида.
`hi` выигрывает, если новая карта выше **или равна** текущей (k = 14 - r вариантов из 13), `lo` если ниже или равна (k = r). Равенство выигрывает для любого направления.
Ход с k = 13 (выигрывает всегда) запрещён сервером (`move_forbidden`). `skip` заменяет текущую карту новой бесплатно и без ограничения числа, множитель и ставка не меняются.
Множитель шага = 13/k * 36/37 (возврат 36/37), множитель партии это произведение; всё считается на сервере точными дробями, выплата `floor(ставка * M)`. Забрать (`cashout`) можно
только после хотя бы одного угаданного хода. Потолок: множитель от 1000 закрывает партию выплатой ровно 1000 ставок (`capped`). Следующая карта выбирается в момент хода и до него
не существует: ни в ответах, ни в базе, ни в логах. Брошенная партия (24 часа без действий, как в минах) закрывается автоматически: без угаданных ходов возврат ставки (`refunded`, `auto`),
иначе выплата по текущему множителю (`cashed`, `auto`). Ставка идёт в `total_staked` при первом ходе `hi`/`lo` (не на старте и не при пропуске). Опыт при закрытии один раз: `bet * (1 - 36/37 / M)`
вниз (не меньше 0), где M: при выводе множитель вывода, при проигрыше множитель проигранного хода (текущий на шаг), при потолке 1000; пропуски и возврат опыта не дают.
Примеры ответов (по ним тест проверяет форму, из них строится мок клиента): `docs/examples/hilo.json`.

**Общая форма ответа** (start, guess, cashout и state; все ключи всегда):

| Поле | Тип | Значение |
|---|---|---|
| status | str | `"active"`, `"lost"`, `"cashed"`, `"capped"`, `"refunded"`, `"none"` (state без партии) |
| bet | int\|null | ставка |
| card | `{rank int, suit str, how str}`\|null | текущая карта (у завершённой партии последняя: у проигранной та, что выпала); `how`: `start`, `win`, `tie` (равенство, выигрыш), `skip`, `lose` |
| history | [card] | до 12 прошлых карт партии, от старых к новым (текущей карты здесь нет) |
| steps | int | число угаданных ходов (пропуски не считаются) |
| multiplier | str | множитель партии «X.YY» (вниз; «1.00» без угаданных ходов) |
| payout_now | int\|null | сколько даст `cashout` сейчас; null, если партия не активна |
| can_cashout | bool | активна и есть хотя бы один угаданный ход |
| moves | `{hi, lo}`\|null | для активной партии: `{available bool, probability str, multiplier str, payout int}`: доступен ли ход, вероятность в процентах с одним знаком («53.8»), множитель (не выше потолка) и выплата после угадывания (с учётом потолка); null, если партия не активна |
| cap | str | потолок множителя («1000.00») |
| payout | int\|null | итоговая выплата завершённой партии (0 при проигрыше); null пока идёт |
| balance | int | всегда |
| level | int | уровень профиля |
| xp | int | весь накопленный опыт |
| auto | bool | партия закрыта автоматически (24 часа без действий) |
| replayed | bool | повтор того же `request_id` (в `state` всегда false) |

Ошибки POST: 400 `{"detail": "invalid_request"}`; 409 `{"detail": "<код>"}`: `active_game_exists`, `insufficient_funds`, `no_active_game`, `move_forbidden`,
`nothing_to_cash_out`, `request_conflict` (тот же `request_id` с другими параметрами или действием). Повтор с тем же `request_id` и параметрами возвращает сохранённый ответ с `replayed: true`.

### POST /api/hilo/start
Группа write. Тело: `{"request_id": str, "bet": int (1..1000000000)}`. 200: общая форма (`status: "active"`, первая карта).

### POST /api/hilo/guess
Группа write. Тело: `{"request_id": str, "choice": "hi"|"lo"|"skip"}`. 200: общая форма.

### POST /api/hilo/cashout
Группа write. Тело: `{"request_id": str}`. 200: общая форма (`status: "cashed"`).

### GET /api/hilo/state
Группа read. 200: общая форма: активная партия, иначе последняя завершённая, иначе `status: "none"`.

## Переводы между участниками беседы
Фишки виртуальные: перевод это подарок между участниками игры. Работает только если приложение открыто из беседы, получатель
выбирается по `member_ref` из рейтинга беседы (идентификаторы Telegram в API не показываются: `member_ref` это HMAC от беседы и игрока секретом сервера).
Константы в `bot/transfers.py`, клиент берёт лимиты из `transfer_limits` в `/api/me`. Примеры ответов: `docs/examples/transfers.json`.
Правила: сумма 100..500000 за перевод; пауза 10 секунд между переводами; отправитель не ниже уровня 3, старше 1 часа с регистрации и с накопленными ставками
(`total_staked`) не менее 20000; за скользящие 24 часа отправитель отправляет не больше 500000 (считается списанное, то есть вместе с комиссией); суточного лимита на получение нет
(ограничивает только потолок баланса получателя `recipient_limit`). Комиссия 5 % (минимум 1) идёт на игровой аккаунт разработчика. Владелец (`OWNER_CHAT_ID`) не подпадает под суточный
лимит отправки и условие по ставкам, комиссию не платит; мин. и макс. сумма, пауза, уровень и возраст аккаунта действуют и для него. Комиссия владельцу в лимиты других не входит.
Перевод не влияет на опыт, уровень и `total_staked`. Значения лежат в `bot/transfers.py`.

### POST /api/transfers/send
Группа write. Тело: `{"request_id": str, "member_ref": str (32 hex), "amount": int (100..500000)}`. 200 (все ключи всегда):
`amount int`, `fee int`, `received int` (сколько получит получатель), `balance int` (баланс отправителя после), `level int`, `daily_left int`, `replayed bool`.
Повтор с тем же `request_id` и параметрами возвращает сохранённый перевод (`replayed: true`, `balance` текущий).
Ошибки: 400 `{"detail": "invalid_request"}`; 409 `{"detail": "<код>"}`: `no_chat`, `self_transfer`, `not_in_chat`, `level_too_low`, `account_too_new`,
`not_enough_staked` (у отправителя ставок меньше порога), `cooldown` (в теле ещё `seconds int`: сколько ждать), `daily_limit` (исчерпан суточный лимит отправки),
`insufficient_funds`, `recipient_limit` (баланс получателя упёрся бы в потолок), `request_conflict`.

### GET /api/transfers
Группа read. 200: `{"items": [{"direction": "out"|"in", "name": str, "amount": int, "fee": int, "time": int}]}`: до 20 последних переводов
(отправленных и полученных), новые первыми; `name` имя второй стороны как в рейтинге. Помечает входящие просмотренными (`incoming_unseen` в `/api/me` обнуляется).

### GET /api/chat/members
Группа read. Список участников беседы для выбора получателя перевода (из рейтинга можно выбрать только топ-10, здесь все).
Запрос: `?q=<часть имени>&offset=<n>`. `q` ищет по имени без учёта регистра по вхождению (до 32 символов, управляющие символы отбрасываются), пустой `q` это все;
`offset` целое 0..100000 (по умолчанию 0). 200: `{"items": [{"name": str, "member_ref": str}], "next_offset": int|null}`: не больше 30 участников на страницу,
по последней активности (новые первыми); себя в списке нет; ни балансов, ни уровней, ни идентификаторов Telegram. `next_offset` null, если страниц больше нет.
Ошибки: 400 `{"detail": "invalid_request"}` (плохой `offset`); 409 `{"detail": "no_chat"}` (приложение открыто вне беседы) или `{"detail": "not_in_chat"}`
(запрашивающего нет среди участников этой беседы). Примеры: `docs/examples/chat_members.json`.

