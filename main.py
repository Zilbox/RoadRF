import config
import time
from datetime import date
import discord # Подключаем библиотеку
from discord.ext import commands
from discord import app_commands
import config, events
import sqlite3
import random
import asyncio
import re

# Класс для автоматической синхронизации команд при запуске
class MyBot(commands.Bot):
    async def setup_hook(self):
        for guild in self.guilds:
            await self.tree.sync(guild=guild)
        print(f"Синхронизировано для {len(self.guilds)} серверов.")


intents = discord.Intents.default()  # Подключаем "Разрешения"
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.members = True
# Задаём префикс и интенты
bot = MyBot(command_prefix=config.PREFIX, intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    await bot.tree.sync()  # Синхронизация слэш-команд с Discord


# Отправка сообщения в канал от лица бота
@bot.tree.command(name="сообщение_в_канал", description='Отправка сообщения в канал')
@app_commands.describe(
    url='Ссылка на канал',
    message='Текст сообщения'
)
async def message_send(interaction: discord.Interaction, url: str, message: str, players: str = '-', message_id: str = ''):
    # Деферим ответ, чтобы сообщить пользователю, что бот работает
    await interaction.response.defer()
    user_id = interaction.user.id
    if user_id not in config.PLAYERS_ADMIN_ID:
        await interaction.followup.send(f"Ну и куда мы лезем?")
    else:
        # Обрабатываем переносы строк
        message = message.replace("\\n", "\n")

        # Получаем текст сообщения, который идет после ссылки на канал
        message_text = message

        # Пытаемся извлечь ID канала из ссылки
        channel_id_match = re.search(r"discord(?:app)?\.com/channels/(\d+)/(\d+)", url)

        if channel_id_match:
            guild_id = int(channel_id_match.group(1))  # ID сервера
            channel_id = int(channel_id_match.group(2))  # ID канала

            # Находим сервер
            guild = bot.get_guild(guild_id)
            if guild:
                # Находим канал по ID
                channel = guild.get_channel(channel_id)
                if channel:
                    # Добавляем пинг игроков в конец сообщения
                    if players != '-':
                        message_text += '\n'
                        players = players.split()
                        for player in players:
                            if player not in ['everyone', 'here']:
                                user = discord.utils.get(guild.members, name=player)
                                if user:
                                    player_id = user.id
                                    message_text += f"<@{player_id}> "
                            else:
                                message_text += f"@{player} "

                    # Отправляем сообщение в канал
                    match = re.match(r"https://discord.com/channels/(\d+)/(\d+)/(\d+)", message_id)
                    if len(message_id) > 0 and match:
                        message_id = int(message_id[message_id.rfind('/')+1:])
                        message = await channel.fetch_message(message_id)
                        await message.reply(message_text)  # Отвечаем на сообщение
                    else:
                        await channel.send(message_text)
                    await interaction.followup.send(f"Сообщение отправлено в канал {channel.mention}.")
                else:
                    await interaction.followup.send("Не удалось найти канал с таким ID.")
            else:
                await interaction.followup.send("Не удалось найти сервер с таким ID.")
        else:
            await interaction.followup.send("Некорректная ссылка на канал.")


# Создание соединения с бд
def create_connection(db_file):
    """ Создает соединение с базой данных SQLite. """
    conn = None
    try:
        conn = sqlite3.connect(db_file)
        return conn
    except sqlite3.Error as e:
        print(f"Ошибка при подключении к БД: {e}")
    return conn


# Проверка существует ли пользователь в бд
def check_member_exists(conn, user_id):
    """ Проверяет, существует ли участник в базе данных. """
    try:
      cursor = conn.cursor()
      cursor.execute("SELECT user_id FROM discord_members WHERE user_id = ?", (user_id,))
      return cursor.fetchone() is not None
    except sqlite3.Error as e:
      print(f"Ошибка при проверке пользователя: {e}")
      return False


# Проверка может ли получить событие на стат
def stat_event_check(conn, user_id):
    """ Проверяет, существует ли участник в базе данных. """
    try:
      cursor = conn.cursor()
      cursor.execute("SELECT stat_date FROM discord_members WHERE user_id = ?", (user_id,))
      db_date = str(cursor.fetchone()[0])
      today = str(date.today())
      result = (abs(int(db_date.split('-')[1]) - int(today.split('-')[1])) +
                abs(int(db_date.split('-')[2]) - int(today.split('-')[2])))
      if result < 1:
          return False
      else:
          return True
    except sqlite3.Error as e:
      print(f"Ошибка при проверке пользователя: {e}")
      return False


# Проверка может ли получить событие на стат
def good_bad_event_check(conn, user_id):
    """ Проверяет, существует ли участник в базе данных. """
    try:
      cursor = conn.cursor()
      cursor.execute("SELECT negative_event_count, positive_event_count FROM discord_members WHERE user_id = ?", (user_id,))
      db_answer = list(cursor.fetchone())
      positive = db_answer[1]
      negative = db_answer[0]
      if positive > 0:
          return '+', positive
      elif negative > 0:
          return '-', negative
      else:
          return '=', 0
    except sqlite3.Error as e:
      print(f"Ошибка при проверке пользователя: {e}")
      return False


# Добавляет пользователя в бд
def add_member(conn, user_id):
    """ Добавляет ID участника в базу данных. """
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO discord_members (user_id, stat_date, positive_event_count, negative_event_count) "
                       "VALUES (?, ?, ?, ?)", (user_id, '2025-01-01', 0, 0))
        conn.commit()
    except sqlite3.Error as e:
        print(f"Ошибка при добавлении пользователя: {e}")


# Собираем пул событий по id каналу и общие
def location_events_collect(stat_event: bool, good_bad_event: str, location: str):
    location_result = []
    # Проверяем есть ли триггер + и - событий
    if good_bad_event == '+':  # триггер + событий есть
        location_result += events.EVENTS[location]['+']
    elif good_bad_event == '-':  # триггер - событий есть
        location_result += events.EVENTS[location]['-']
    elif good_bad_event == '=':  # триггера + и - событий нет
        location_result += events.EVENTS[location]['+'] + events.EVENTS[location]['-'] + events.EVENTS[location]['=']
        # Добавляем события на стат если игрок не получал такое событие сегодня
        if stat_event:
            location_result += events.EVENTS[location]['stat']
    return location_result


# Вычитаем из БД кол-во триггеров + и - событий
def good_bad_event_change(conn, user_id, good_bad_event: str, good_bad_event_count: int):
    try:
        if good_bad_event == '+':
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE discord_members SET positive_event_count = ? WHERE user_id = ?;
            """, (good_bad_event_count, user_id))
            conn.commit()
        else:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE discord_members SET negative_event_count = ? WHERE user_id = ?;
            """, (good_bad_event_count, user_id))
            conn.commit()
    except sqlite3.Error as e:
        print(f"Ошибка при добавлении пользователя: {e}")


# При получении события на стат записываем текущую дату для игрока
def stat_event_input(conn, user_id):
    try:
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE discord_members SET stat_date = ? WHERE user_id = ?;
        """, (str(date.today()), user_id))
        conn.commit()
    except sqlite3.Error as e:
        print(f"Ошибка при добавлении пользователя: {e}")

# Собираем пул событий
def road_event_pool(stat_event: bool, good_bad_event: str, start: str, end: str):
    # Задаем зерно для рандома
    random.seed(time.time())
    # Собираем пул событий
    result_list = []
    # События начальной точки
    result_list += location_events_collect(stat_event, good_bad_event, start)
    # События конечной точки
    result_list += location_events_collect(stat_event, good_bad_event, end)
    # События общие
    result_list += location_events_collect(stat_event, good_bad_event, 'all')
    # Перемешиваем набор событий
    random.shuffle(result_list)
    # Выбираем случайное событие
    result = random.choice(result_list)
    return result


# Создаем кнопки для событий
class ButtonView(discord.ui.View):
    def __init__(self, button_names, answers, stat_event, *, timeout=360):
        super().__init__(timeout=timeout)
        self.button_names = button_names
        self.answers = answers
        self.stat_event = stat_event
        self.user_id = None # Запоминаем пользователя
        for button in self.button_names:
            button = discord.ui.Button(label=button, custom_id=button, style=discord.ButtonStyle.secondary)
            button.callback = self.on_button_click
            self.add_item(button)


    async def on_button_click(self, interaction: discord.Interaction):
        if interaction.user.id == self.user_id:
            await interaction.response.defer()
            button_id = interaction.data.get('custom_id')
            i = 0
            for button in self.button_names:
                if button_id == button:
                    # Если событие дает стат, обновляем дату в БД для игрока
                    if self.answers[i]['stat']:
                        stat_event_input(conn, self.user_id)
                    # Если событие тригеррит + или - события, то записываем это в БД
                    if self.answers[i]['triggers+'] > 0:
                        good_bad_event_change(conn, self.user_id, '+', self.answers[i]['triggers+'])
                    elif self.answers[i]['triggers-'] > 0:
                        good_bad_event_change(conn, self.user_id, '-',self.answers[i]['triggers-'])

                    if 'buttons' in self.answers[i]:
                        # Задаем зерно для рандома
                        random.seed(time.time())
                        # Записываем название кнопок
                        buttons = self.answers[i]['buttons']
                        # Собираем пул событий
                        events = []
                        for button in buttons:
                            result_list = []
                            for event in self.answers[i]['reactions'][button]:
                                if self.stat_event and event['stat']:
                                    continue
                                else:
                                    result_list.append(event)
                            # Перемешиваем набор событий
                            random.shuffle(result_list)
                            # Выбираем случайные события
                            events.append(random.choice(result_list))
                        # Создаем элемент кнопки
                        view = ButtonView(
                            buttons,
                            events,
                            self.stat_event
                        )  # Передаем событие в view
                        view.user_id = self.user_id  # Запоминаем пользователя, что бы не было нажимания кнопок другими пользователями.

                        # Добавляем текст события
                        answer = self.answers[i]['text']

                        # Тегаем игрока
                        answer += f'\n<@{self.user_id}>'

                        # Добавляем файлы если есть
                        files = [discord.File(config.ARTS_PATH + image) for image in self.answers[i]['file']]

                        await interaction.followup.send(answer, view=view, files=files)
                        break
                    else:
                        # Добавляем текст события
                        answer = self.answers[i]['text']

                        # Тегаем игрока
                        answer += f'\n<@{self.user_id}>'

                        # Добавляем файлы если есть
                        files = [discord.File(config.ARTS_PATH + image) for image in self.answers[i]['file']]

                        await interaction.followup.send(answer, files=files)
                        break
                i += 1
            self.stop()


# Создание события в дороге
async def generate_event_send_and_respond(interaction: discord.Interaction, start: str, end: str):
    # Проверяем, является ли сообщение личным сообщением
    if isinstance(interaction.channel, discord.DMChannel):
        await interaction.followup.send(config.DMCHANNEL_MSG)
    else:
        user_id = interaction.user.id  # ID пользователя

        # id стартовой локации
        if start.lower() != 'рф':
            start = str(start[start.rfind('/') + 1:])
        # id конечной локации
        if end.lower() != 'рф':
            end = str(end[end.rfind('/') + 1:])
        start_name = ''
        end_name = ''
        for key, val in config.CHANNELS_ID_MAIN_TO_SECONDARY.items():
            if start in val:
                start_name = config.START_END_TRUE_NAMES[start]
                start = key
            if end in val:
                end_name = config.START_END_TRUE_NAMES[end]
                end = key

        stat_event = True
        # проверяем существует ли пользователь
        if not check_member_exists(conn, user_id):
            add_member(conn, user_id)
            print(f"Добавлен пользователь с ID: {user_id}")
        else: # если существует - получаем может ли он получить событие на стат
            stat_event = stat_event_check(conn, user_id)
            print(f"Пользователь с ID: {user_id} уже существует.")

        # Проверка на тригерр + и - событий
        good_bad_event, good_bad_event_count = good_bad_event_check(conn, user_id)

        # Получение случайного события для игрока
        event_for_player =  road_event_pool(stat_event, good_bad_event, start, end)

        # Если событие без кнопок
        if event_for_player['type'] == "text":
            # Если событие дает стат, обновляем дату в БД для игрока
            if event_for_player['stat']:
                stat_event_input(conn, user_id)

            # Если был тригерр, то уменьшаем его
            if good_bad_event != '=':
                good_bad_event_change(conn, user_id, good_bad_event, good_bad_event_count-1)

            # Если событие тригеррит + или - события, то записываем это в БД
            if event_for_player['triggers+'] > 0:
                good_bad_event_change(conn, user_id, '+', event_for_player['triggers+'])
            elif event_for_player['triggers-'] > 0:
                good_bad_event_change(conn, user_id, '-', event_for_player['triggers-'])


            # Ответ для игрока
            answer = ''

            # Добавляем в ответ откуда и куда
            answer += f'***При путешествии из "{start_name}" в "{end_name}"...***\n'

            # Добавляем в ответ в какой части пути произошло событие
            if event_for_player['class'] == start:
                answer += '*В начале пути...*\n'
            elif event_for_player['class'] == end:
                answer += '*В конце пути...*\n'
            else:
                answer += '*В середине пути...*\n'

            # Добавляем текст события
            answer += event_for_player['text']

            # Тегаем игрока
            answer += f'\n<@{user_id}>'

            # Добавляем файлы если есть
            files = [discord.File(config.ARTS_PATH + image) for image in event_for_player['file']]

            await interaction.followup.send(answer, files=files)

        elif event_for_player['type'] == "button":
            # Задаем зерно для рандома
            random.seed(time.time())
            # Записываем название кнопок
            buttons = event_for_player['buttons']
            # Собираем пул событий
            events = []
            for button in buttons:
                result_list = []
                for event in event_for_player['reactions'][button]:
                    if stat_event and event['stat']:
                        continue
                    else:
                        result_list.append(event)
                # Перемешиваем набор событий
                random.shuffle(result_list)
                # Выбираем случайные события
                events.append(random.choice(result_list))
            # Создаем элемент кнопки
            view = ButtonView(
                buttons,
                events,
                stat_event
            )  # Передаем событие в view
            view.user_id = user_id  # Запоминаем пользователя, что бы не было нажимания кнопок другими пользователями.

            # Ответ для игрока
            answer = ''

            # Добавляем в ответ откуда и куда
            answer += f'***При путешествии из "{start_name}" в "{end_name}"...***\n'

            # Добавляем в ответ в какой части пути произошло событие
            if event_for_player['class'] == start:
                answer += '*В начале пути...*\n'
            elif event_for_player['class'] == end:
                answer += '*В конце пути...*\n'
            else:
                answer += '*В середине пути...*\n'

            # Добавляем текст события
            answer += event_for_player['text']

            # Тегаем игрока
            answer += f'\n<@{user_id}>'

            # Добавляем файлы если есть
            files = [discord.File(config.ARTS_PATH + image) for image in event_for_player['file']]

            await interaction.followup.send(answer, view=view, files=files)

@bot.tree.command(name='предел', description='Выдает случайное дорожное событие')
@app_commands.describe(
    start='Начальная точка',
    end='Конечная точка'
)
async def event_send(interaction: discord.Interaction, start: str, end: str):
    await interaction.response.defer()  # Откладываем ответ
    if ('https://discord.com' in start or 'рф' in start.lower()) and ('https://discord.com' in end or 'рф' in end.lower()):
        if start == end:
            await interaction.followup.send('Проверь ссылки. Ты едешь в место, из которого уезжаешь!')
        else:
            asyncio.create_task(generate_event_send_and_respond(interaction, start, end))  # Создаем фоновую задачу
    else:
        await interaction.followup.send('Вот ты ввел не пойми что - и получил не пойми что...')


database_file = "RF.db" # Имя файла БД
conn = create_connection(database_file)
if conn:
    bot.run(config.TOKEN)