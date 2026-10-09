# Контракт API

Форма ответов закреплена тестом `bot/test_api_contract.py` (настоящее приложение, точный набор ключей и типы).
Меняя ответ, обновляйте вместе: сервер, этот файл, тест и клиент (`js/`). Значения ниже заглушки,
реальных данных нет.

## Общее

- Адрес: `API_URL` в `js/00-config.js`. Заголовок каждого запроса: `Authorization: tma <initData>` (подписанная строка Telegram).
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
| gems | int | всегда: баланс кристаллов (премиум-валюта, покупается за Stars, раздел «Кристаллы»); 0, если кристаллов нет |
| cosmetics | `{equipped {слот: код}, show_in_rating bool, patina {слот: стадия}, complete_sets [код коллекции]}` | всегда: косметика: надетое по 8 слотам и показ рамки и значка в рейтинге. Необязательное поле `patina` присутствует только при надетой патине и только для надетых слотов (значения соответствуют достигнутым порогам: `chip_patina` (chip, засечки за краш ≥×50: 1, 5, 15, 40), `back_patina` (card_back, сыграно раундов: 200, 1000, 3000, 8000), `mine_patina` (mine_icons, взрывов мин: 10, 40, 120, 300)) |
| chat | `{in_chat bool, bonus_pct int, active_today int, boost_until int\|null, boost_gems int}` | всегда: атрибуция к беседе, активные участники сегодня и бусты |

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

## Бонус беседы к ферме и бусты
Игрок привязывается к той беседе, из которой последней открыл игру (атрибуция). Бонус фермы зависит от числа активных сегодня (по московскому времени) игроков этой беседы и купленных бустов. Бонус уже включён в `farm.income_per_hour` и `rate` (`/api/me`).

### POST /api/chat/boost
Группа write. Покупка буста беседы за кристаллы. Тело: `{"request_id": str}`.
200: `{"bonus_pct" int, "boost_until" int, "gems" int (баланс после покупки)}`.
Ошибки: 400 `invalid_request`; 409 `no_chat` (открыто не из беседы), `boost_cap_reached` (активные бусты уже дают максимум +25 %, кристаллы не списываются), `not_attributed` (игрок привязан к другой беседе по атрибуции), `insufficient_gems`, `request_conflict`; 429. Пример: `docs/examples/chat_boost.json`.

## GET /api/chat/top
Группа read. Вне группового чата (личная переписка) 200 `{"scope": "none"}`: **других полей нет**.
В беседе 200:

| Поле | Тип | Наличие |
|---|---|---|
| scope | str | всегда (`"chat"`) |
| top | [объект] | всегда, до 10 элементов |
| me | объект | всегда |
| chat_staked | int | всегда |
| chat_level | int | всегда |
| chat_points | int | всегда |
| chat_level_start_points | int | всегда (очки, с которых начинается текущий уровень; прогресс внутри уровня = (chat_points - chat_level_start_points) / (chat_next_points - chat_level_start_points)) |
| chat_next_points | int\|null | всегда (null на максимальном уровне) |
| set_names | объект | всегда |

Элемент `top`: `rank int`, `name str`, `balance int`, `is_me bool`, `staked int`, `level int`, `complete_sets [str]` (все всегда).
`me`: `rank int`, `balance int`, `total int` (число участников рейтинга), `staked int`, `level int` (все всегда).
Поле `set_names`: объект, сопоставляющий код коллекции с её названием (берётся из реестра коллекций).
Элемент `top` дополнительно содержит `cosmetics {слот: код}`: только публичные слоты (`avatar_frame`, `badge`), только надетые не стартовые предметы и только если игрок не отключил показ (`show_in_rating`); иначе `{}`. Остальные слоты других игроков не отдаются никогда. Если надета рамка `frame_patina`, добавляется `avatar_frame_stage` (0..4): стадия потемнения по стажу аккаунта (30 / 90 / 180 / 365 дней). Поле `complete_sets` содержит список кодов полностью собранных коллекций игрока (если показ не отключён через `show_in_rating`, иначе `[]`). Пример: `docs/examples/cosmetics.json` (`rating_entry`).

## GET /api/chat/best-wins
Группа read, только чтение (игрока не регистрирует). Рекорды выигрыша беседы: личный рекорд игрока это лучший чистый выигрыш за один раунд (выплата минус вся ставка раунда) по всем играм; одна запись на игрока, обновляется только если новый выигрыш строго больше. Проигрыш, ничья и возврат ставки рекорд не пишут; переводы, `/give`, `/grantall`, доход фермы и покупки не считаются. Участники беседы определяются так же, как в `/api/chat/top`. Вне группового чата 200 `{"scope": "none"}`: других полей нет. В беседе 200 (все ключи всегда):

| Поле | Тип | Наличие |
|---|---|---|
| scope | str | всегда (`"chat"`) |
| top | [объект] | всегда, до 10 элементов: сумма по убыванию, при равенстве раньше достигший |
| me | объект\|null | место вызвавшего среди участников с рекордом; null, если своего рекорда нет |
| total | int | сколько в беседе участников с рекордом |

Элемент `top`: `rank int`, `name str`, `net_amount int`, `game str` (код игры: `roulette`, `mines`, `keno`, `blackjack`, `crash`, `hilo`, `slot`; клиент показывает русское название и неизвестные коды игнорирует), `is_me bool`), `cosmetics {слот: код}` (рамка и значок по тем же правилам видимости, что в `/api/chat/top`). `me`: `rank int`, `net_amount int`, `game str`, `total int`.
В ответе нет `telegram_id`, времени достижения и идентификатора беседы. Отдельный маршрут, а не поле в `/api/chat/top`: сбой или рост рекордов не ломает основной рейтинг. Кэша нет (`no-store`), как у остальных ответов API. Примеры: `docs/examples/best_wins.json`. Ошибки: 401 `{"detail": "Unauthorized"}`, 429 `{"error": "too_many_requests"}`.

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
Блэкджек платит 3:2 (`bet + bet*3//2`). Достигнув 21 (твёрдого или мягкого) после `hit`, рука завершается сама как `stand`: дилер доигрывает в том же ответе (`status: "finished"`), действия игрока не нужны; при 21 в `actions` бывает только `stand` (раздача, начатая до этого правила), `hit` и `double` недоступны. Колода и скрытая карта дилера в ответах никогда не присутствуют. Раздача закрывается автоматически
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
`invalid_action` (double не на первых двух картах; hit при 21; состояние не меняется), `request_conflict` (тот же `request_id` с другими параметрами). Повтор с тем же
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
**Автовывод:** ставка с целью T (`target_x100`) тоже остаётся активным раундом и закрывается тем, что наступит раньше по серверному времени: ручной вывод (`cashout`) на текущем множителе m(t); достижение цели (выплата по T, не выше); крах в точке C. Если T ≤ C, цель достигается не позже краха (равенство выигрывает), иначе раунд проигран, когда множитель превысил C. Ручной вывод до цели платит m(t) (то же округление, что у ручного режима), после цели или краха `cashout` отвечает итогом раунда (выплата по T или проигрыш), выше T заплатить нельзя. Закрытие ленивое: при следующем чтении (`state`, `/api/me`, `start`, `cashout`) по серверному времени, результат тот же, что был бы вовремя; повторные и параллельные запросы дают одну выплату.
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
| auto | bool | раунд закрыт сервером по времени (разбился, достиг цели или предела, брошен), а не вашим выводом |
| replayed | bool | повтор того же `request_id` (в `state` всегда false) |

Выплата: `bet * c // 100` (c в сотых). Вывод ровно на множителе краха выигрывает. Ошибки POST: 400 `{"detail": "invalid_request"}`; 409 `{"detail": "<код>"}`:
`active_game_exists`, `insufficient_funds`, `no_active_game`, `too_early`, `request_conflict`, `balance_limit`.

## Живой краш (Live Crash)

В отличие от старого краша, это общая игра для всего сервера. Состояние читается частым опросом (`GET /api/crash/live`) в группе лимитов `live` (например, 6 запросов в секунду на игрока). Маршруты ставок и вывода в группе `write`. Точка краха и секрет раскрываются только в фазе итога и попадают в историю закрытых раундов. Имена игроков берутся из участников беседы (если открыто из чата) и видны всем в ленте этой беседы (вне беседы видна только своя ставка). Рекомендуемый интервал опроса клиента: 300 мс в приёме ставок и полёте, 1000 мс в паузе итога.

### GET /api/crash/live
Возвращает состояние общего раунда и ленту ставок вашей комнаты. Подпись обязательна.
**Параметры запроса:**
- `v`: (необязательный) токен состояния, полученный из предыдущего ответа.

**Ответ (изменилось состояние):**
- `server_ms`: int (текущее время сервера в мс)
- `v`: str (короткий токен состояния комнаты и раунда)
- `history`: список последних закрытых раундов `[{"crash_x100": int, "seed_hash": str, "seed": str}, ...]`
- `round`: объект или null:
  - `id`: int
  - `phase`: `"betting"`, `"flight"`, `"result"`, `"idle"`
  - `seed_hash`: str (хэш секрета)
  - `bet_open_ms`: int
  - `flight_start_ms`: int
  - `m100`: int\|null (текущий множитель в полёте)
  - `result`: объект `{"crash_x100": int, "seed": str}`\|null (только в фазе `result`)
  - `next_open_ms`: int\|null (время начала следующего раунда, только в фазе `result`)
- `me`: объект `{"bet": int, "target_x100": int|null, "status": str, "cashed_x100": int|null, "payout": int|null}`\|null
- `bets`: список `[{"name": str, "bet": int, "status": str, "cashed_x100": int|null, "payout": int|null}, ...]` (в беседах имена из чата, вне бесед общая анонимная лента «Игрок N»)

**Ответ (состояние не изменилось, если переданный `v` совпадает):**
- `unchanged`: `true`
- `v`: str (тот же токен)
- `server_ms`: int (текущее время сервера)
- `phase`: str (текущая фаза раунда)
- `m100`: int\|null (текущий множитель в полёте)

### POST /api/crash/live/bet
Тело: `{"request_id": str, "bet": int, "target_x100": int|null}`. `target_x100` необязательное (ключ можно опустить).
Ответ 200: `{"round_id": int, "bet": int, "target_x100": int|null, "balance": int, "replayed": bool}`
Ошибки (409): `betting_closed`, `already_bet`, `room_full`, `insufficient_funds`, `request_conflict`, `balance_limit`.
Ошибки (400): `invalid_request` (ставка или цель вне диапазона).

### POST /api/crash/live/cashout
Тело: `{"request_id": str}`.
Ответ 200: `{"round_id": int, "cashed_x100": int, "payout": int, "balance": int, "replayed": bool}`
Ошибки (409): `no_bet`, `too_early`, `round_over`, `request_conflict`.
Ошибки (400): `invalid_request`.

### POST /api/crash/start, POST /api/crash/cashout, GET /api/crash/state
Прежний краш (партия на игрока) закрыт 2026-10-09, живой краш заменил его: все три адреса всегда отвечают 410 `{"detail": "gone"}` без проверки подписи. Партия, открытая на момент выкладки, закрывается возвратом ставки (при следующем `GET /api/me` игрока или при почасовом проходе); история старых партий хранится 30 дней, попадает в `/mydata` и удаляется как раньше.

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

## Western Slot
Каскадный слот раздела «Не слоты» (сборка в `games/western-slot/`, исходники в проекте casinch). Правила и генератор целиком на сервере (`bot/slot.py`, порт движка
casinch с пресетом original-like): 6 барабанов высотой 3-4-5-5-4-3, выплаты «способами» (`ways`), выигравшие ячейки взрываются, символ в золотой рамке становится Wild,
множитель x1, x2, x4 ... x1024 по шагам каскада (в бесплатных вращениях с x8), 3 и более Scatter в итоговом поле дают 10 бесплатных вращений (+2 за каждый сверх трёх),
покупка бонуса стоит 75 ставок и гарантирует 3-5 Scatter, выигрыш раунда не больше 5000 ставок (`capped`). Ставка = 20 монет, `coin` из списка
`1, 2, 5, 10, 25, 50, 100, 250, 500, 2500, 10000, 25000, 50000` (ставка 20..1 000 000 фишек). Один запрос = весь раунд, включая бесплатные вращения: списание, затем зачисление
всего выигрыша в одной транзакции; клиент только проигрывает присланное. Опыт: `cost * 0,718` за спин, `cost * 0,028` за покупку (`xp.slot_xp`). Примеры: `docs/examples/slot.json`.

### POST /api/slot/spin
Группа write. Тело: `{"request_id": str, "coin": int (из списка), "buy": bool}`. 200:

| Поле | Тип | Наличие |
|---|---|---|
| coin | int | всегда |
| bought | bool | всегда (покупка бонуса) |
| cost | int | всегда (списано: `20 * coin`, при покупке `1500 * coin`) |
| payout | int | всегда (`round.totalWin * coin`, 0 при проигрыше) |
| round | объект | всегда (раунд в формате движка, ниже) |
| balance | int | всегда |
| level | int | всегда (уровень профиля) |
| xp | int | всегда (весь накопленный опыт) |
| replayed | bool | всегда |

`round` (ключи как в типах движка casinch, суммы в единицах, монета = 1): `base` спин, `freeSpins` [спин], `freeSpinsLeftAfter` [int] (остаток вращений после каждого
бесплатного), `totalWin int`, `bonusWin int`, `capped bool`, `bought bool`. Спин: `mode` (`"base"`|`"fs"`), `steps` [шаг] (не меньше одного), `scatters int` (в итоговом поле),
`freeSpinsAwarded int`, `totalWin int`, `capped bool`. Шаг: `board str` (поле до оценки), `wins` [`{sym str, length int, ways int, pay int}`], `multiplier int`,
`stepWin int` (`pay` с множителем), `exploded` и `toWild` [`{reel int, row int}`], `refilled` [`{reel int, cells str}`] (новые ячейки сверху барабана),
`boardAfter str` (поле после досыпки; у последнего шага равно `board`). Запись поля: барабаны слева направо через `|`, ячейки сверху вниз, символы
`B R H V A K Q J` платящие, `S` Scatter, `W` Wild, `*` после символа = золотая рамка (`K A H | R A V H | B* R J A H* | R J V H Q | J Q R V | K B A`).

Повтор с тем же `request_id` и теми же `coin` и `buy` возвращает сохранённый раунд с `replayed: true` (`balance`, `level`, `xp` текущие).
Ошибки: 400 `{"detail": "invalid_request"}` (типы, `coin` вне списка, `buy` не bool, ключи, `request_id`); 409 `{"detail": "insufficient_funds"}`,
`{"detail": "request_conflict"}` (тот же `request_id` с другими `coin` или `buy`), `{"detail": "balance_limit"}` (баланс плюс наибольшая выплата `100000 * coin` выше потолка).

## Переводы удалены
Переводов между игроками больше нет (план экономики, этап E1). `POST /api/transfers/send`, `GET /api/transfers` и `GET /api/chat/members` отвечают
410 `{"detail": "gone"}` без проверки подписи; из `/api/me` убраны `incoming_unseen` и `transfer_limits`, из рейтинга и рекордов `member_ref`.
Уже сделанные переводы лежат в таблице `transfers` до конца срока хранения (30 дней), попадают в `/mydata` и удаляются или обезличиваются
`/deletemydata` как раньше.

## Косметика
Только внешний вид; не влияет на шансы, выплаты, множители, XP, лимиты, ферму и экономику. Каталог в коде (`bot/cosmetics.py`): 8 слотов (`card_back`, `chip`, `table`, `mine_icons`, `keno_ball`, `crash`,
`avatar_frame`, `badge`), в каждом один стартовый предмет (в базе не хранится: если для слота нет записи, действует стартовый). Предметы не передаются между игроками. Предмет попадает к игроку одним из способов (поле `source`): выдача владельцем командой `/giveitem` (`owner_gift`), покупка за фишки (`chips`, `POST /api/cosmetics/buy`) или покупка за Telegram Stars (`stars`).
**Коллекции** (`docs/COLLECTIONS.md`): `GET /api/cosmetics/mine` отдаёт `collections`: `[{"code", "name", "how" (как получить), "season": [с, по] (московские даты) или null, "parts": [коды предметов], "owned" int, "total" int, "complete" bool}]`. Части коллекции это предметы каталога без цены (`price` null): они не продаются и не дарятся (покупка и подарок отвечают `item_unavailable`), выдаются только бесплатно, источник `collection`. Достижения «Клуб ×1.01» (`achievement`, `docs/COLLECTIONS.md`): `achv_nearly`, `achv_sapper`, `achv_bust`, `achv_keno` выдаются автоматически за провалы в играх (учёт в той же транзакции игры), без цены, не покупаются и не дарятся. «Листопад»: за награду дня на 3-й, 5-й и 7-й день серии входов, пока идёт сезон (октябрь 2026), следующая недостающая часть; после сезона выдача прекращается, у получивших части остаются. Ответ сбора награды дня содержит `collection_part` (`{"collection", "collection_name", "part", "name"}` или null). «Дачный сезон»: за уровни дохода фермы (источник farm), сезон null (всегда). Пороги уровней дохода: 2 (back_rug), 4 (chip_cork), 6 (table_oilcloth), 9 (mine_beetle), 12 (keno_lotto), 16 (crash_barrel).
**За Stars покупаются только кристаллы** (раздел «Кристаллы»), предметы оформления покупаются за кристаллы или за фишки (`POST /api/cosmetics/buy`, валюта берётся из цены в каталоге). Счета на предметы за Stars больше не создаются (`POST /api/cosmetics/invoice` отвечает 410); счета, выставленные раньше, бот ещё принимает по прежней цене. Фишки можно только купить, продать или вывести их нельзя.
Внутренние `payment_ref` и `charge_id` (идентификатор платежа) в API не отдаются. Примеры: `docs/examples/cosmetics.json`.

### GET /api/cosmetics/catalog
Группа read. 200: `{"slots": [{"slot", "name", "starter", "public"}], "items": [{"code", "slot", "name", "description", "rarity" ("starter"|"common"|"rare"|"premium"), "price" ({"currency": "gems"|"chips", "amount": int} или null), "starter bool", "available bool"}], "sets": [{"code", "name", "price_gems", "parts": [str]}]`.
Цена есть только у восьми доступных нестартовых предметов (константы в `bot/cosmetics.py`, `PRICES`); у стартовых и недоступных `price` null.
Одинаков для всех игроков. `available: false`: предмет в каталоге есть, но надеть его нельзя (`item_unavailable`).

### GET /api/cosmetics/mine
Группа read. 200: `{"owned": [{"code", "source", "acquired_at"}], "equipped": {слот: код}, "show_in_rating": bool}`. `owned` без стартовых; `equipped` по всем слотам. Предметы «Патина» больше не выдаются при открытии гардероба: это набор за 1000 кристаллов (`POST /api/cosmetics/buy-set`, `set_code` `patina`), части по 400 кристаллов отдельно; выданное раньше остаётся. В `cosmetics.patina` (ответ `/api/me`, `equip`, `unequip`) у рамки ключ `avatar_frame`.

### POST /api/cosmetics/equip
Группа write. Тело: `{"request_id", "slot", "code"}`. 200: `{"slot", "code", "equipped": {слот: код}, "replayed": bool}`. Надеть можно только свой или стартовый предмет и только в его слоте; стартовый предмет равен «снять».
Ошибки: 400 `invalid_request`; 404 `unknown_item`; 409 `slot_mismatch`, `item_unavailable`, `not_owned`, `request_conflict` (тот же `request_id` с другими параметрами); 429 (не чаще одной смены в секунду на игрока, `Retry-After: 1`, тело `{"error": "too_many_requests"}`, как у общего лимитера).

### POST /api/cosmetics/unequip
Группа write. Тело: `{"request_id", "slot"}`. 200: `{"slot", "code" (стартовый), "equipped", "replayed"}`. Слот без надетого предмета не ошибка. Ошибки те же (кроме `unknown_item`, `slot_mismatch`, `not_owned`).

### POST /api/cosmetics/visibility
Группа write. Тело: `{"request_id", "show_in_rating": bool}`. 200: `{"show_in_rating", "replayed"}`. Выключенный показ скрывает рамку и значок игрока в рейтинге беседы. Ошибки: 400, 409 `request_conflict`, 429.

### POST /api/cosmetics/buy-set
Группа write. Покупка набора косметики (сразу все части) за кристаллы. Тело: `{"request_id", "set_code"}`. 200: `{"set_code", "items": [коды частей], "price_gems", "balance", "gems", "replayed"}`.
Одна транзакция: неизвестный набор (ошибка 404 `unknown_set`); если хотя бы одна часть уже есть, ошибка 409 `already_owned` (до списания); затем списание кристаллов (ОДНИМ вызовом на сумму `price_gems`) и выдача всех частей. Идемпотентно.

### POST /api/cosmetics/buy
Группа write. Покупка предмета за кристаллы или за фишки (по цене из каталога). Тело: `{"request_id", "item_code"}`. 200: `{"item_code", "price": {"currency": "gems"|"chips", "amount"}, "balance" (фишки после покупки), "gems" (кристаллы после покупки), "replayed"}`.
Одна транзакция: проверка предмета, «уже есть» до списания, затем `wallet.gems_debit` (причина `cosmetic_purchase`) или `wallet.debit` (с начисленным доходом), выдача (источник `gems` или `chips`); опыт, `total_staked` и уровень не меняются. Идемпотентно по `request_id`.
Ошибки: 400 `invalid_request`; 404 `unknown_item`; 409 `item_unavailable` (недоступен или стартовый), `already_owned`, `insufficient_chips`, `insufficient_gems`, `request_conflict`; 429.

### POST /api/cosmetics/invoice
Удалён (план экономики, E2): всегда 410 `{"detail": "gone"}`. За Stars покупаются кристаллы: `POST /api/gems/invoice`.

## Кристаллы
Премиум-валюта (план `docs/ECONOMY.md`, этап E2). Покупается пакетами за Telegram Stars; не продаётся, не выводится, не обменивается и не передаётся между игроками. Баланс отдаёт `/api/me` (поле `gems`), журнал изменений `gems_ledger` (append-only), баланс всегда равен его сумме; запись только через `wallet.gems_credit/gems_debit` (причина из закрытого списка `economy_config.GEM_REASONS`, класс source или sink). Цены и пакеты лежат в `bot/economy_config.py`.

### GET /api/gems/packs
Группа read. 200: `{"packs": [{"code" ("gems_*"), "stars" int, "gems" int}], "gems" int}`: пакеты по возрастанию цены и баланс кристаллов игрока. Цены клиент берёт только отсюда.

### POST /api/gems/invoice
Группа write. Оплата пакета Telegram Stars. Тело: `{"request_id", "pack_code"}`. 200: `{"invoice_url", "replayed"}` (ссылка из `createInvoiceLink`, валюта XTR, пустой `provider_token`, одна цена; в `payload` подписанная метка без личных данных).
Не чаще одной ссылки на игрока и пакет за 10 секунд (429 `too_many_requests`, `Retry-After`; тот же `request_id` возвращает ту же ссылку). Ошибки: 400; 404 `unknown_pack`; 502 `invoice_failed`; 503 `payments_unavailable`.
Оплату принимает бот: `pre_checkout_query` (цена пакета должна совпасть), `successful_payment` (одна транзакция: запись в `gem_purchases` с уникальным `charge_id` и начисление, повторная доставка ничего не создаёт), потолок баланса `economy_config.GEMS_MAX_BALANCE` (оплата возвращается автоматически).
Возврат пакета: `/refund <платёж>` (владелец) возможен, пока кристаллы пакета не потрачены (после платежа не было списаний и баланс не меньше пакета); потраченные возвращает только `/refund <платёж> force`. Примеры: `docs/examples/gems.json`.

## Фишки за кристаллы
Покупка фишек (план экономики, этап E6). Фишки можно только купить: продать, вывести, обменять на деньги, Stars, подарки и ценности или передать другому игроку нельзя. Пакет равен нескольким часам фермы игрока: фишек в пакете = часы × доход в час игрока (не меньше часов × 100, не больше 10 000 000). Покупка не даёт опыт, не считается ставкой и не меняет уровень. Пакеты, цены и лимит лежат в `bot/economy_config.py` (`CHIP_PACKS`, `CHIP_PACK_DAILY_LIMIT`).

### GET /api/chips/packs
Группа read. 200: `{"packs": [{"code" ("chips_*"), "gems" int, "hours" int, "chips" int}], "gems" int, "balance" int, "daily_left" int}`: пакеты по возрастанию цены с числом фишек именно для этого игрока, его кристаллы, фишки и сколько покупок осталось за скользящие 24 часа.

### POST /api/chips/buy
Группа write. Тело: `{"request_id", "pack_code"}`. 200: `{"pack_code", "gems_spent", "chips", "balance" (фишки после покупки), "gems" (кристаллы после покупки), "daily_left", "replayed"}`.
Одна транзакция: повтор по `(игрок, request_id)`, суточный лимит, начисление дохода, `wallet.gems_debit` (причина `chip_purchase`), `wallet.credit`, запись в `chip_purchases` (365 дней). Повтор того же `request_id` отдаёт то же без списания.
Ошибки: 400 `invalid_request`; 404 `unknown_pack`; 409 `insufficient_gems`, `daily_limit`, `balance_limit` (фишки не поместились бы под потолок баланса, кристаллы не списываются), `request_conflict`; 429. Примеры: `docs/examples/chips.json`.

## Серия входов
Ежедневная награда (`docs/ECONOMY_ADDITIONS.md`, п. 3). «День» по московскому времени (UTC+3, `economy_config.STREAK_UTC_OFFSET_HOURS`). Дни 1-6 цикла: фишки по нарастающей (300, 400, 500, 600, 800, 1000), день 7: те же 1000 фишек и 5 кристаллов; следующий цикл щедрее на 25 % (потолок x2, кристаллов до 8).
Пропуск дня откатывает на день 1 текущего цикла (если неделя была закончена, начинается следующий цикл). Бесплатные кристаллы не больше 100 за календарный месяц (`FREE_GEMS_MONTHLY_CAP`), сверх этого кристаллы урезаются (`gems_capped`). Опыт и ставки не меняются.

### GET /api/streak
Группа read. 200: `{"claimed_today" bool, "streak_day" int (1..7: день цикла, который даст ближайший сбор; после сегодняшнего сбора это завтрашний), "cycle" int, "reward": {"chips" int, "gems" int}, "week": [{"day", "chips", "gems"}] (семь дней текущего цикла), "seconds_to_next_day" int}`.

### POST /api/streak/claim
Группа write. Тело пустое: `{}`. 200: `{"streak_day", "cycle", "chips", "gems", "gems_capped" bool, "collection_part" (объект или null: выданная часть коллекции), "balance" (фишки после), "gems_balance" (кристаллы после), "replayed" bool}`. Один раз в «день» (запись `streak_claims` с ключом игрок и день); повтор в тот же день отдаёт то же с `replayed: true` и ничего не начисляет.
Ошибки: 400 `invalid_request` (непустое тело); 429. Примеры: `docs/examples/streak.json`.

## Подарки косметикой
Предмет за кристаллы покупается сразу на имя участника той же беседы (`docs/ECONOMY_ADDITIONS.md`, п. 2). Получатель задаётся непрозрачной меткой `ref` (HMAC от беседы и игрока; Telegram id клиент не видит). Нельзя дарить фишки, кристаллы и пакеты, предмет за фишки, предмет, который у получателя уже есть, и самому себе; подаренное нельзя передарить, продать или обменять; не больше 5 подарков в сутки (`economy_config.GIFT_DAILY_LIMIT`). Кристаллы, потраченные на подарок, считаются потраченными (возврат пакета, из которого они оплачены, недоступен). У получателя предмет приходит с `source: "gift"` и в `GET /api/cosmetics/mine` с полем `gift_from` (имя дарителя на момент подарка, пусто, если даритель удалил данные); бот присылает получателю короткое уведомление (ошибка отправки не мешает подарку).

### GET /api/gifts/recipients
Группа read. Нужен запуск из беседы. 200: `{"recipients": [{"name", "ref"}] (до 30 участников той же беседы с профилем, без самого игрока), "daily_left" int, "gems" int}`. Ошибки: 409 `no_chat` (приложение открыто не из беседы), `not_in_chat`.

### POST /api/gifts/send
Группа write. Тело: `{"request_id", "ref", "item_code"}`. 200: `{"item_code", "price": {"currency": "gems", "amount"}, "recipient" (имя), "gems" (кристаллы отправителя после), "daily_left", "replayed"}`. Одна транзакция: проверки до списания, `wallet.gems_debit` (причина `gift_purchase`), запись `gifts`, выдача предмета получателю (источник `gift`). Идемпотентно по `request_id`.
Ошибки: 400 `invalid_request`; 404 `unknown_item`, `unknown_recipient` (метка не найдена в этой беседе или у участника нет профиля); 409 `no_chat`, `not_in_chat`, `self_gift`, `not_for_gems`, `item_unavailable`, `already_owned`, `daily_limit`, `insufficient_gems`, `request_conflict`; 429. Примеры: `docs/examples/gifts.json`.


## Реферальная система (E5)
Приглашённые игроки регистрируются по ссылке. Пригласивший получает награду за квалификацию приглашённого и 30 % от выигрыша казино у него в течение 90 дней после квалификации (до 300 000 фишек на одного приглашённого): процент зачисляется в той же транзакции, что раунд приглашённого. Основатель беседы получает награду, когда бота добавили в группу и беседа стала живой. Подробности и числа: docs/ECONOMY.md, раздел 6. Ссылка приложения и код приглашения не меняются.

### GET /api/referral
Группа read. Получение своей реферальной ссылки и статистики приглашённых. 200 (все ключи всегда):
| Поле | Тип | Значение |
|---|---|---|
| link | str\|null | ссылка вида `https://t.me/Bot/app?startapp=ref_КОД`; null, если базовая ссылка в настройках не задана |
| invited | int | сколько всего игроков привязаны по вашей ссылке |
| qualified | int | сколько из них прошли квалификацию (играют >= 24 часов, уровень >= 3, сыграно >= 10 раундов) |
| commission_earned | int | сколько фишек пригласивший уже получил процентом от приглашённых (за всё время) |
| milestones | list | вехи наград-предметов: `[{"count": 3, "item": "ref_scout", "reached": bool}, ...]` (3 / 10 / 30 квалифицированных; предмет выдаётся сам при квалификации) |
| rules | dict | параметры экономики для отображения: `invitee_chips`, `inviter_chips`, `inviter_gems`, `qualify_hours`, `qualify_level`, `qualify_rounds`, `commission_pct`, `commission_days`, `commission_cap`, `founder_chips`, `founder_gems`, `founder_players`, `founder_level` |

## Выгрузка данных (/mydata)
Бот отправляет JSON-файл `mydata.json` со всеми собранными данными игрока. Новое поле `referral` содержит статистику по приглашениям:
`"referral": {"invited_by_someone": bool, "invited_count": int, "qualified_count": int, "commission_earned": int, "founded_chats": int, "founded_chats_rewarded": int}` (без чужих идентификаторов и без самого кода; `founded_chats`: сколько групп вы добавили ботом).
