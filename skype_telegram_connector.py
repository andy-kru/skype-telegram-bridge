import logging
from io import BytesIO
from multiprocessing import Process
from time import sleep

import telegram
from bs4 import BeautifulSoup
from skpy import SkypeEventLoop, SkypeNewMessageEvent, SkypeTextMsg, SkypeImageMsg, Skype, SkypeMsg, \
    SkypeEditMessageEvent, SkypeFileMsg, SkypeUser, SkypeChat
from telegram.error import NetworkError, Unauthorized
from tinydb import TinyDB, Query

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)
db = TinyDB('db.json')

skype_chat_id = '19:xxx@thread.skype'
skype_login = 'xxx@outlook.com'
skype_password = 'xxx'
skype_id = 'live:xxx'
skype_bot = Skype(skype_login, skype_password, 'token.cache')
skype_channel = skype_bot.chats.chat(skype_chat_id)
telegram_bot = telegram.Bot('xxxxx:xxxxx')
telegram_chat_id = '-xxx'


def get_skype_signature(event):
    return '<code>' + str(event.msg.user.name) + ' via Skype</code>'


def get_telegram_signature(update):
    return SkypeMsg.bold(SkypeMsg.italic(update.message.from_user.full_name + ' via Telegram'))


def persist_message_event(telegram_msg, skype_msg):
    db.insert({
        'telegram-chat-id': telegram_msg.chat_id,
        'telegram-id': telegram_msg.message_id,
        'skype-chat-id': skype_msg.chatId,
        'skype-id': skype_msg.id,
        'skype-client-id': skype_msg.clientId
    })


def get_telegram_message(search_field, value):
    result = db.search(Query()[search_field] == value)
    if result:
        return result[0]['telegram-id']
    else:
        return None


class SkypeBridge(SkypeEventLoop):
    def __init__(self):
        super(SkypeBridge, self).__init__(skype_login, skype_password)

    def onEvent(self, event):
        if isinstance(event, SkypeNewMessageEvent) or isinstance(event, SkypeEditMessageEvent):

            msg = None
            try:
                msg = event.msg
                # logger.info("chatId: " + msg.chatId)
                if not (msg.chatId == skype_chat_id and not msg.userId == self.userId):
                    return
            except TypeError:
                if isinstance(event, SkypeEditMessageEvent):
                    message_id = get_telegram_message('skype-client-id', event.raw['resource']['skypeeditedid'])
                    if message_id:
                        telegram_bot.delete_message(telegram_chat_id, message_id - 1)
                        telegram_bot.delete_message(telegram_chat_id, message_id)
                    return

            if isinstance(event, SkypeEditMessageEvent) and not msg.content:
                message_id = get_telegram_message('skype-client-id', msg.clientId)
                if message_id:
                    telegram_bot.delete_message(telegram_chat_id, message_id)

            elif isinstance(msg, SkypeTextMsg):
                content = msg.content.replace('&apos;', '\'')
                soup = BeautifulSoup(content, 'html.parser')
                reply_id = None
                result = None

                if isinstance(event, SkypeEditMessageEvent) or soup.quote:
                    if soup.quote:
                        reply_id = get_telegram_message('skype-id', soup.quote['messageid'])
                        if reply_id:
                            soup.quote.decompose()
                        else:
                            soup.select("quote > legacyquote")[0].string = soup.quote.attrs['authorname'] + ':\n'
                            soup.select("quote > legacyquote")[1].string = '\n- - -\n'
                    content = soup.text

                text = content + '\n' + get_skype_signature(event)

                if isinstance(event, SkypeEditMessageEvent):
                    message_id = get_telegram_message('skype-client-id', msg.clientId)
                    if message_id:
                        result = telegram_bot.edit_message_text(chat_id=telegram_chat_id,
                                                                text=text,
                                                                message_id=message_id,
                                                                parse_mode=telegram.ParseMode.HTML)
                else:
                    result = telegram_bot.send_message(chat_id=telegram_chat_id,
                                                       text=text,
                                                       parse_mode=telegram.ParseMode.HTML,
                                                       reply_to_message_id=reply_id,
                                                       disable_notification=True)
                persist_message_event(result, msg)
            elif isinstance(msg, SkypeImageMsg) or isinstance(msg, SkypeFileMsg):
                telegram_bot.send_message(chat_id=telegram_chat_id,
                                          text=get_skype_signature(event) + ':',
                                          parse_mode=telegram.ParseMode.HTML,
                                          disable_notification=True)
                if isinstance(msg, SkypeImageMsg):
                    result = telegram_bot.send_photo(chat_id=telegram_chat_id,
                                                     photo=BytesIO(msg.fileContent),
                                                     disable_notification=True)
                else:
                    result = telegram_bot.send_document(chat_id=telegram_chat_id,
                                                        document=BytesIO(msg.fileContent),
                                                        filename=msg.file.name,
                                                        disable_notification=True)
                persist_message_event(result, msg)


def telegram_polling():
    try:
        update_id = telegram_bot.get_updates()[0].update_id
    except IndexError:
        update_id = None

    while True:
        try:
            for update in telegram_bot.get_updates(update_id, 10):
                update_id = update.update_id + 1
                message = update.message

                if str(message.chat_id) == telegram_chat_id:
                    if message.text or message.sticker or message.caption:
                        text = message.text_html
                        if message.reply_to_message:
                            quote = SkypeMsg.quote(SkypeUser(id=skype_id), SkypeChat(id=skype_chat_id),
                                                   message.reply_to_message.date, message.reply_to_message.text_html)
                            text = quote + text
                        if message.sticker:
                            text = message.sticker.emoji
                        if message.caption:
                            text = message.caption_html
                        content = text + '\n' + get_telegram_signature(update)
                        skype_channel.sendMsg(content=content, rich=True)
                        result = skype_channel.getMsgs()[0]
                        persist_message_event(message, result)
                    if message.photo:
                        file_id = message.photo[-1].file_id
                        photo = BytesIO(telegram_bot.getFile(file_id).download_as_bytearray())
                        if not message.caption:
                            message = get_telegram_signature(message) + ':'
                            skype_channel.sendMsg(content=message, rich=True)
                        skype_channel.sendFile(content=photo, name=file_id, image=True)
                        result = skype_channel.getMsgs()[0]
                        persist_message_event(message, result)
        except NetworkError:
            sleep(1)
        except Unauthorized:
            update_id += 1


if __name__ == '__main__':
    telegram_process = Process(target=telegram_polling)
    telegram_process.start()
    skype_process = SkypeBridge()
    skype_process.loop()
