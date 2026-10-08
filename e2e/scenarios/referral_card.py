"""Реферальная карточка в профиле."""
from harness import check

NAME = "referral_card"
USERS = {"me": {"rate": 0}}
SP = "(s) => s.replace(/\\s/g, ' ')"

async def run(w):
    p = w.page
    
    # 1. Профиль не открыт, карточка должна быть скрыта и не должно быть запросов
    check("до открытия профиля нет запросов", await p.ev("E.count('/api/referral')"), 0)
    
    # Открываем профиль
    await p.tap("[data-tab='profile']")
    await p.wait("!document.getElementById('referral-card').hidden", 10, "карточка рефералки")
    
    # Запрос был ровно один
    check("запрос ушел", await p.ev("E.count('/api/referral')"), 1)
    
    # Ссылка null (так как нет GAME_LINK на сервере), проверяем текст
    check("сообщение без ссылки", await p.ev("document.getElementById('referral-link-text').textContent"), "Ссылка приглашения пока недоступна")
    check("кнопки скрыты", await p.ev("document.getElementById('referral-actions').hidden"), True)
    
    # Закрываем профиль
    await p.tap("[data-tab='play']")
    
    # Теперь подменим ответ fetch в браузере, чтобы вернуть ссылку
    await p.ev("""
        window._old_fetch_ref = window.fetch;
        window.fetch = async (u, o) => {
            if (u.includes('/api/referral')) {
                window.__refCalls = (window.__refCalls || 0) + 1;
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({
                        link: "https://t.me/TestBot/app?startapp=ref_CODE",
                        invited: 5,
                        qualified: 2,
                        rules: {
                            invitee_chips: 500,
                            inviter_chips: 1000,
                            inviter_gems: 5,
                            qualify_hours: 24,
                            qualify_level: 3,
                            qualify_rounds: 10
                        }
                    })
                };
            }
            return window._old_fetch_ref(u, o);
        };
    """)
    
    # Перемотаем время (limit 60 секунд)
    await p.ev('refLastRequest -= 61000')      # прошло больше минуты с прошлого запроса
    
    # Открываем профиль снова
    await p.tap("[data-tab='profile']")
    
    # Ждем, когда появятся кнопки
    await p.wait("!document.getElementById('referral-actions').hidden", 10, "появились кнопки рефералки")
    
    # Проверяем текст правил и числа
    text = await p.ev(f"({SP})(document.getElementById('referral-text').textContent)")
    check("текст правил", text, "Друг получит 500 фишек при первом входе. Когда он проживёт 24 ч, дойдёт до 3 уровня и сыграет 10 раундов, ты получишь 1000 фишек и 5 💎.")
    check("приглашено", await p.ev("document.getElementById('referral-invited').textContent"), "5")
    check("квалифицировано", await p.ev("document.getElementById('referral-qualified').textContent"), "2")
    
    # Нажимаем "Отправить другу"
    await p.tap("#referral-send")
    links = await p.ev("window.__links || []")
    check("openTelegramLink был вызван", len(links) > 0, True)
    check("ссылка share", "t.me/share/url?url=" in links[0], True)
    
    # Проверка "Скопировать ссылку" (подменяем буфер на отказ)
    await p.ev("""
        Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
            writeText: () => Promise.reject(new Error("deny"))
        }});
    """)
    await p.tap("#referral-copy")
    await p.wait("!document.getElementById('referral-link-text').hidden", 10, "текст ссылки при отказе буфера")
    check("текст ссылки", await p.ev("document.getElementById('referral-link-text').textContent"), "https://t.me/TestBot/app?startapp=ref_CODE")
    
    # после показа профиля с подменой запрос был ровно один; повторный показ в течение минуты нового не делает
    check("после минуты: один запрос к подмене", await p.ev("window.__refCalls || 0"), 1)
    await p.tap("[data-tab='play']")
    await p.tap("[data-tab='profile']")
    await p.wait("!document.querySelector('[data-screen=profile]').hidden", 5, "профиль снова открыт")
    check("в течение минуты нового запроса нет", await p.ev("window.__refCalls || 0"), 1)

    # Возвращаем fetch (не обязательно, но вежливо)
    await p.ev("window.fetch = window._old_fetch_ref;")

