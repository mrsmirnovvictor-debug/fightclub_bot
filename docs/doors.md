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


## Управление и больница — `vcpd_hospital_district.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/vcpd_hospital_district.png

- **vcpd** — VCPD: `406,416  505,407  505,505  407,514`
- **hospital** — Больница: `437,1081  578,1094  576,1177  436,1161`


## Автошкола и страховая — `driving_school_insurance_district.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/driving_school_insurance_district.png

- **driving_school** — Автошкола: `285,378  406,356  400,454  289,477`
- **insurance_office** — Страховая компания: `516,950  639,984  637,1088  516,1050`


## Автосалон — `car_dealership.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/car_dealership.png

- **car_dealership** — Автосалон: `325,592  428,592  426,684  326,684`


## Деловой угол — `gym_office_district.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/gym_office_district.png

- **strength_gym** — Тренажёрный зал: `527,456  660,439  658,542  528,557`
- **office_building** — Офисное здание: `558,1135  670,1118  672,1210  557,1232`


## Учебный квартал — `police_school_medical_college.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/police_school_medical_college.png

- **police_school** — Школа полиции: `388,403  563,408  563,516  389,510`
- **medical_college** — Медицинский колледж: `319,1119  545,1185  546,1267  320,1205`


## Армейская часть — `military_base_training_ground.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/military_base_training_ground.png

- **military_base** — Армейская часть: `394,363  528,370  524,463  395,449`
- **indoor_training_ground** — Крытый полигон: `356,1436  504,1458  504,1530  355,1503`


## Кадетский городок — `cadet_corps_dormitory.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/cadet_corps_dormitory.png

- **cadet_corps** — Кадетский корпус: `428,379  548,378  548,467  427,466`
- **dormitory** — Общежитие: `407,1134  528,1138  526,1206  404,1206`


## Турнирная арена — `fight_tournament_stadium.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/fight_tournament_stadium.png

- **fight_tournament_stadium** — Турнирная арена: `405,603  541,608  539,703  406,692`


## Жилой квартал — `residential_district.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/residential_district.png

- **residential_apartment** — Жилой дом №1: `313,405  394,381  394,459  312,482`
- **residential_apartment_2** — Жилой дом №2: `681,463  760,490  755,567  682,539`
- **residential_apartment_3** — Жилой дом №3: `111,922  203,890  203,985  111,1018`
- **residential_apartment_4** — Жилой дом №4: `739,956  822,1006  821,1098  739,1048`


## Особняк мафии — `mafia_mansion.png`

https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev/locations/mafia_mansion.png

- **mafia_mansion** — Особняк мафии: `449,455  528,462  529,554  448,544`


## Жилой квартал: четыре дома и один вид изнутри

На карте квартала нарисованы четыре дома, и дверь у каждого своя —
поэтому домов в справочнике тоже четыре: `residential_apartment` и
`residential_apartment_2…4`. Вид изнутри у них один на всех: внутри они
одинаковые, и заводить четыре одинаковые картинки незачем. Работать они
будут одинаково — когда своё жильё вообще появится.
