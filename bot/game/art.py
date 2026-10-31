"""Где лежат картинки клуба.

Один адрес на весь проект: и оружие, и щиты, и образы бойцов живут в общем
бакете R2, просто в разных папках. Меняется хранилище — правится одна строка.
"""

from __future__ import annotations

BUCKET = "https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev"

WEAPONS = f"{BUCKET}/weapons"
# Второй заход по недостающим позициям лёг в отдельную папку
ADDED = f"{BUCKET}/add"
AVATARS = f"{BUCKET}/avatars"
# Лица нанятых и городской охраны. Лежат отдельной папкой внутри
# образов: это не образ игрока — выбрать их нельзя, они приходят
# вместе со ступенью найма
NPC_AVATARS = f"{AVATARS}/npc"
# Подложки пустых слотов. Папку переименовали в `placeholders`: «слоты»
# в бакете путались со слотами зала, а подложка — это и есть плейсхолдер
SLOTS = f"{BUCKET}/placeholders"
ITEMS = f"{BUCKET}/items"
# Футболки — самый молодой раздел, его рисовали отдельным заходом
SHIRTS = f"{BUCKET}/shirts"
# Сеты клубной лавки: папка на класс, внутри папка на набор, внутри по
# файлу на слот — items_new/tank/bouncer/jacket.png. Раскладка чужая, но
# правило в ней есть, поэтому адрес считается, а не пишется руками
SETS = f"{BUCKET}/items_new"
# Рейд-боссы
BOSSES = f"{BUCKET}/bosses"
# Вещи рейдов: билеты, пропуска и прочее, что рисовали вместе с боссом, а
# не с лавкой. Папка легла рядом с портретами и зовётся по очереди рейдов
RAID_ITEMS = f"{BOSSES}/raid2_items"
# Жители города: бармен и прочие, кто стоит в доме и говорит. Рисуют их
# с прозрачным фоном — они ложатся поверх вида изнутри, а не вместо него
NPC = f"{BUCKET}/npc"
# Товар лавки мага
MAGIC = f"{BUCKET}/magic"
POTIONS = f"{BUCKET}/potions"
# Карты районов города: по карте на район
LOCATIONS = f"{BUCKET}/locations"
# Картинки к вопросам билета ПДД. Бакет у них свой, не клубный: билет
# прислали отдельной выгрузкой, и перекладывать её к вещам не наше дело
PDD = "https://pub-ea6a4494c019470aa38328eec255511d.r2.dev/pdd2"
# Виды изнутри: по картинке на дом. Папки две, и это не наша прихоть —
# первые четырнадцать домов выгрузили в locations/interiors, следующие
# шестнадцать легли рядом с корнем. Переименовывать чужой бакет не наше
# дело, поэтому у дома написано, в какой он папке
INTERIORS = f"{LOCATIONS}/interiors"
# Районы второй очереди: участок, больница, казармы и всё остальное
NEW_INTERIORS = f"{BUCKET}/interiors"
# Кибернетическое оружие рейда на военную базу: папка внутри оружейной.
# Четыре вида на класс, шестнадцать файлов, и имя файла совпадает с кодом
# вещи — так их и выгрузили
FUTURISTIC = f"{WEAPONS}/futuristic"
# Зарядные колонки АЗС: по картинке на колонку. Папка внутри районов —
# так их и выгрузили, вместе с картами домов
CHARGERS = f"{LOCATIONS}/charging_stations"
# Знаки кланов: по картинке на знак, и знак один на клан
CLANS = f"{BUCKET}/clans"
# Значки городских служб: полиция, ФБР, армия, медицина, администрация и
# мэр. Рисуют их с прозрачным фоном — значок встаёт у ника рядом со
# знаком клана, и носить их разом нельзя: служба и семья несовместимы
SERVICES = f"{BUCKET}/services"


def avatar(code: str, ext: str = "jpeg") -> str:
    """Картинка из папки образов под своим кодом: avatars/fan_boss.jpeg.

    Остаётся ради тех, кто лежит в этой папке, но образом игрока не
    является: лица гопников рисовали вместе с фанатской линией одежды, и
    легли они сюда же. Образы бойцов берут `look_avatar`.

    Формат приходится называть: лица гопников выгрузили в jpeg, а лицо
    охраны военной базы — в jpg. Перегонять чужой бакет не наше дело,
    поэтому формат пишется у портрета, как у карт районов.
    """
    return f"{AVATARS}/{code}.{ext}"


def look_avatar(code: str) -> str:
    """Образ бойца: avatars/rookie_transparent.png.

    Фон у образа свой, прозрачный: на карточке он ложится поверх машины,
    и непрозрачный квадрат её бы закрыл. Отсюда и png вместо jpeg —
    прозрачности в фотоформате нет.
    """
    return f"{AVATARS}/{code}_transparent.png"


def npc_avatar(code: str) -> str:
    """Лицо нанятого по его коду: avatars/npc/mafia_9_tank.png."""
    return f"{NPC_AVATARS}/{code}.png"


def clan_sign(code: str) -> str:
    """Знак клана лежит под своим кодом: clans/black_ravens.png."""
    return f"{CLANS}/{code}.png"


def service_badge(code: str) -> str:
    """Значок службы лежит под её кодом: services/police.png."""
    return f"{SERVICES}/{code}.png"


def slot(name: str) -> str:
    """Подложка пустого слота по имени файла: placeholders/weapon.png.

    Обычно имя совпадает с кодом слота, но не всегда: клетка второй руки
    рисуется щитом, а клетка «тело» — силуэтом футболки. Поэтому сюда
    приходит имя файла целиком, с расширением.

    Подложки лежат в png, а не в jpeg: это плоские силуэты в 256 пикселей,
    и такую картинку png жмёт лучше фотоформата. Фон у них свой, тёмный —
    прозрачность оказалась не по зубам генератору, — поэтому на светлой теме
    вёрстка их приглушает. Не загрузилась — слот покажет значок, как и раньше.
    """
    return f"{SLOTS}/{name}"


def shirt(code: str) -> str:
    """Футболка лежит под кодом вещи: shirts/club_tee.jpeg."""
    return f"{SHIRTS}/{code}.jpeg"


def item(code: str) -> str:
    """Вещь лежит под своим кодом: items/riot_shield.jpeg.

    Так адрес картинки не приходится писать руками: новая вещь получает
    его от собственного кода. Старые файлы с именами вроде
    `bandana.jpeg_202608281514.jpeg` под правило не подходят — у таких
    вещей адрес до сих пор задан явно, и `Item.image` его перебивает.
    """
    return f"{ITEMS}/{code}.jpeg"


def set_piece(line: str, kit: str, slot: str) -> str:
    """Вещь из сета: items_new/trickster/cardsharp/jacket.png.

    Три части адреса — линия класса, название набора и слот. Линия не
    всегда совпадает с кодом класса: папку трикстера назвали
    `trickster`, хотя класс в коде зовётся `rogue`.

    Формат png, а не jpeg: пак рисовали позже остальной лавки и отдали
    целиком в png.
    """
    return f"{SETS}/{line}/{kit}/{slot}.png"


def army_piece(slot: str) -> str:
    """Вещь военного набора: items_new/military_energy/helmet.png.

    Папка без ступени, в отличие от `set_piece`: набор один, уровень у
    него один, и делить его на ступени нечего — это форма охраны базы, а
    не лестница прилавка.
    """
    return f"{SETS}/military_energy/{slot}.png"


def soldier_piece(name: str) -> str:
    """Вещь солдата охраны: items_new/soldier/pistol.jpg.

    Папка рядом с формой (`army_piece`), но своя и в jpeg: оружие
    караула рисовали отдельным заходом и отдали в другом формате.
    """
    return f"{SETS}/soldier/{name}.jpg"


def energy_weapon(code: str) -> str:
    """Кибернетическое оружие: weapons/futuristic/warrior_energy_sword.png.

    Имя файла совпадает с кодом вещи: шестнадцать клинков выгрузили
    именами, по которым их и зовут в игре, и считать адрес от кода тут
    честнее, чем выписывать его каждой вещи руками.
    """
    return f"{FUTURISTIC}/{code}.png"


def plain_ring(kit: str, name: str) -> str:
    """Кольцо вне наборов: items_new/rings/starter/strength.png.

    Четыре простых кольца первого уровня и три сильных девятого линиям
    классов не принадлежат, и папка у них своя: не `items_new/<класс>`, а
    `items_new/rings/<партия>`. Имя файла называет, что кольцо даёт, —
    `strength`, `health`, — поэтому оно приходит сюда, а не считается от
    кода вещи: у «кольца баланса» код свой, а картинка общая с партией.
    """
    return f"{SETS}/rings/{kit}/{name}.png"


def location(code: str, ext: str = "jpeg") -> str:
    """Карта района: locations/main_hub.jpeg.

    Формат приходится называть: первую очередь города отдали в jpeg,
    вторую — в png. Перегонять её в другой формат незачем, поэтому
    формат написан у района, а имя файла всё так же считается от кода.
    """
    return f"{LOCATIONS}/{code}.{ext}"


def interior(code: str, folder: str = INTERIORS, ext: str = "jpeg") -> str:
    """Вид изнутри дома: locations/interiors/pharmacy_interior.jpeg.

    Снаружи дом — кусочек нарисованного района, и с карты видно только
    дверь. Внутри же боец проводит всё время, и без картинки лавка от
    аптеки отличается одним заголовком.

    Папку приходится называть: интерьеры выгружали двумя заходами и в
    разные места. Формат — тоже: первые очереди отдали в jpeg, четвёртую
    в png. Имя файла при этом всё так же считается от кода дома.
    """
    return f"{folder}/{code}.{ext}"


def charger(kind: str) -> str:
    """Колонка АЗС: locations/charging_stations/ev_charger_fast.jpg."""
    return f"{CHARGERS}/ev_charger_{kind}.jpg"


def boss(code: str) -> str:
    """Портрет рейд-босса: bosses/cellar_boss.png."""
    return f"{BOSSES}/{code}.png"


def pdd(number: int) -> str:
    """Картинка к вопросу билета: pdd2/7.png.

    Номер, а не код: вопросы в билете идут по порядку, и файлы названы
    этим же порядком — так их и прислали.
    """
    return f"{PDD}/{number}.png"


def raid_gear(line: str, code: str) -> str:
    """Вещь рейдовой линии: bosses/raid2_items/fan_boss/items/<код>.png.

    Бакет разбирают по рейдам, и вещи стычки переезжают в папку своей
    линии. Папка линии не всегда зовётся как приставка кода: трикстерская
    называется `fan_trickster`, а вещи в ней — `fan_rogue_*`. Поэтому
    линия приходит отдельным словом, а не берётся из кода.
    """
    return f"{RAID_ITEMS}/{line}/items/{code}.png"


def raid_item(name: str) -> str:
    """Вещь рейда: bosses/raid2_items/match_ticket.png.

    Имя файла приходит сюда, а не считается от кода: билет на матч лежит
    под своим названием, а пропуск в игре зовётся `stadium_pass`.
    """
    return f"{RAID_ITEMS}/{name}.png"


def potion(code: str) -> str:
    """Склянка лежит под кодом эликсира: potions/heal_small.jpeg."""
    return f"{POTIONS}/{code}.jpeg"


__all__ = [
    "ADDED",
    "AVATARS",
    "BOSSES",
    "BUCKET",
    "INTERIORS",
    "ITEMS",
    "LOCATIONS",
    "MAGIC",
    "NEW_INTERIORS",
    "NPC",
    "PDD",
    "POTIONS",
    "RAID_ITEMS",
    "SERVICES",
    "SETS",
    "SHIRTS",
    "SLOTS",
    "WEAPONS",
    "avatar",
    "boss",
    "interior",
    "item",
    "location",
    "look_avatar",
    "plain_ring",
    "pdd",
    "potion",
    "raid_gear",
    "raid_item",
    "set_piece",
    "shirt",
    "slot",
]
