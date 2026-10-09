# Двери второй очереди: снятая разметка

Десять новых карт, девятнадцать домов. Двери сняты с самих картинок, по
четырём углам видимого проёма, в пикселях исходника `941 × 1672`.
Пересчёт в доли — `x = px / 941`, `y = py / 1672`; руками его делать не
нужно:

    python scripts/doors.py --code 406 416 505 407 505 505 407 514

выдаёт готовые строки для `bot/game/locations.py`, а

    python scripts/doors.py --check

проверяет, что ни один угол не уехал за край картинки и что одна и та же
дверь не досталась двум домам сразу.

Размечен сам проём, а не фасад и не крыльцо: область касания и так шире
двери на 2.5% ширины карты по горизонтали и 1.5% по вертикали — это
делается в коде и в разметку не входит.

До этого двери стояли по шаблону: верхнему дому карты доставалась дверь
магазина одежды, нижнему — дверь аптеки. Палец в них попадал, но
подсветка садилась на косяк как придётся.

## Армейская часть — `military_base_training_ground.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/military_base_training_ground.png

- **military_base** — Армейская часть: `394,363  528,370  524,463  395,449`
- **indoor_training_ground** — Крытый полигон: `402,826  494,838  494,895  402,880` — переснято: прежняя разметка стояла у нижнего края карты, а проём у этого дома посередине.


## Кадетский городок — `cadet_corps_dormitory.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/cadet_corps_dormitory.png

- **cadet_corps** — Кадетский корпус: `428,379  548,378  548,467  427,466`
- **dormitory** — Общежитие: `407,1134  528,1138  526,1206  404,1206`


## Автошкола и страховая — `driving_school_insurance_district.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/driving_school_insurance_district.png

- **driving_school** — Автошкола: `285,378  406,356  400,454  289,477`
- **insurance_office** — Страховая компания: `516,950  639,984  637,1088  516,1050`


## Участок и больница — `vcpd_hospital_district.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/vcpd_hospital_district.png

- **vcpd** — Полицейский участок: `388,403  563,408  563,516  389,510`
- **hospital** — Больница: `319,1119  545,1185  546,1267  320,1205`


## Деловой угол — `gym_office_district.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/gym_office_district.png

- **strength_gym** — Тренажёрный зал: `527,456  660,439  658,542  528,557`
- **office_building** — Офисное здание: `558,1135  670,1118  672,1210  557,1232`


## Жилой квартал — `residential_district.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/residential_district.png

- **residential_apartment** — Жилой дом №1: `313,405  394,381  394,459  312,482`
- **residential_apartment_2** — Жилой дом №2: `681,463  760,490  755,567  682,539`
- **residential_apartment_3** — Жилой дом №3: `111,922  203,890  203,985  111,1018`
- **residential_apartment_4** — Жилой дом №4: `739,956  822,1006  821,1098  739,1048`


## Автосалон — `car_dealership.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/car_dealership.png

- **car_dealership** — Автосалон: `325,592  428,592  426,684  326,684`


## Учебный квартал — `police_school_medical_college.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/police_school_medical_college.png

- **police_school** — Полицейская академия: `406,416  505,407  505,505  407,514`
- **medical_college** — Медицинский колледж: `437,1081  578,1094  576,1177  436,1161`


## Турнирная арена — `fight_tournament_stadium.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/fight_tournament_stadium.png

- **fight_tournament_stadium** — Турнирная арена: `405,603  541,608  539,703  406,692`


## Особняк мафии — `mafia_mansion.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/mafia_mansion.png

- **mafia_mansion** — Особняк мафии: `449,455  528,462  529,554  448,544`

## Тюрьма и таксопарк — `prison_taxi_park_v3.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/prison_taxi_park_v3.png

- **prison** — Тюрьма: `420,662  553,662  551,760  421,758`
- **taxi_depot** — Таксопарк: `282,1022  383,984  380,1045  280,1082` — переснято: прежняя разметка обводила саму створку в двадцать два пикселя, и подсветка на телефоне сходилась в полоску. Теперь это вся голубая панель пешеходного входа в левом корпусе; ремонтные боксы в неё не входят.


## HR и ювелирный — `hr_jewelry_district_v3.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/hr_jewelry_district_v3.png

- **hr_agency** — HR-агентство: `410,422  513,403  511,501  411,518`
- **jewelry_store** — Ювелирный магазин: `493,1074  676,1106  673,1254  492,1215`


## Университетский кампус — `university_campus_v3.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/university_campus_v3.png

- **university** — Университет: `411,463  647,523  645,568  410,507` — центральная группа дверей наверху парадной лестницы.


## Администрация города — `city_administration_v3.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/city_administration_v3.png

- **city_administration** — Администрация: `402,641  504,635  502,793  401,800` — стеклянные двери внутри золотого портала, без наружной рамы.

## Жилой квартал: четыре дома и один вид изнутри

На карте квартала нарисованы четыре дома, и дверь у каждого своя —
поэтому домов в справочнике тоже четыре: `residential_apartment` и
`residential_apartment_2…4`. Вид изнутри у них один на всех: внутри они
одинаковые, и заводить четыре одинаковые картинки незачем. Работать они
будут одинаково — когда своё жильё вообще появится.

## Коды остались от прежних названий

Дом и район участка зовутся `vcpd` и `vcpd_hospital_district`, а
полицейская академия — `police_school`: так названы файлы в хранилище.
На вывесках при этом «Полицейский участок» и «Полицейская академия», и
разойтись это не может — название берётся из справочника, а адрес
картинки считается от кода.
