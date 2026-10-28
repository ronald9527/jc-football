#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞彩中文队名 ↔ 英文队名 权威映射（单一真相源）。

用途：
  - fetch_whoscored.py 服务端把 WhoScored 英文队名翻译成竞彩中文队名，
    从而精确匹配「竞彩编号」（之前因别名表太稀疏，20/21 场 WhoScored 比赛
    拿不到竞彩编号，用户筛选「只看竞彩」几乎为空）。
  - template.html 的 SP_TEAM_ZH 由本表生成，保证显示与服务端一致。

设计原则：
  - 仅当翻译后的中文名 == 某条竞彩比赛的 home/away（且日期容差内）才算命中，
    因此本表可以「宁多勿漏」地覆盖各大联赛——多写的条目不会造成误匹配。
  - 归一化：小写、去重音、去句点/"·"、剥离常见俱乐部前缀后缀
    （FC/FK/AC/AS/US/UD/CD/SK/SV/BK/RC/CF/AFC/RSC/SL/SC/EC/TC/KV/KRC/NK/HNK/GNK/IFK/YB/RB 等），
    用于兜底匹配 WhoScored 的其它拼写变体。
"""
import re
import unicodedata

# ---- 紧凑源：英文（含常见变体） -> 竞彩中文名 ----
# 一个条目可写多个英文别名（用 | 分隔），全部映射到同一中文名。
_TEAM_SRC = """
曼联|Manchester United|Man Utd
曼彻斯特城|Manchester City|Man City
利物浦|Liverpool
阿森纳|Arsenal
切尔西|Chelsea
托特纳姆热刺|Tottenham|Tottenham Hotspur|Spurs
纽卡斯尔联|Newcastle|Newcastle United
西汉姆联|West Ham|West Ham United
阿斯顿维拉|Aston Villa
埃弗顿|Everton
水晶宫|Crystal Palace
布莱顿|Brighton|Brighton & Hove Albion
伯恩茅斯|Bournemouth|AFC Bournemouth
富勒姆|Fulham
布伦特福德|Brentford
诺丁汉森林|Nottingham Forest
伍尔弗汉普顿|Wolverhampton|Wolves
莱斯特城|Leicester|Leicester City
利兹联|Leeds|Leeds United
南安普敦|Southampton
伊普斯维奇|Ipswich|Ipswich Town
桑德兰|Sunderland
伯恩利|Burnley
谢菲尔德联|Sheffield United|Sheffield Utd
斯托克城|Stoke|Stoke City
米德尔斯堡|Middlesbrough
女王公园巡游者|Queens Park Rangers|QPR
西布罗姆维奇|West Brom|West Bromwich Albion
卡迪夫城|Cardiff|Cardiff City
赫尔城|Hull|Hull City
雷克斯汉姆|Wrexham
沃特福德|Watford
诺维奇|Norwich|Norwich City
考文垂|Coventry|Coventry City
朴次茅斯|Portsmouth
米尔沃尔|Millwall
牛津联|Oxford United
德比郡|Derby|Derby County
布莱克本|Blackburn|Blackburn Rovers
普利茅斯|Plymouth|Plymouth Argyle
斯托克波特|Stockport
卢顿|Luton|Luton Town
韦康比|Wycombe|Wycombe Wanderers
查尔顿|Charlton|Charlton Athletic
雷丁|Reading
博尔顿|Bolton
维冈竞技|Wigan|Wigan Athletic
罗瑟勒姆|Rotherham
加的夫|Cardiff
布莱克浦|Blackpool
哈德斯菲尔德|Huddersfield
米德尔斯堡|Middlesbrough
伯明翰|Birmingham|Birmingham City
布里斯托尔城|Bristol City
米尔顿凯恩斯|Milton Keynes|MK Dons
阿斯顿维拉|维拉
曼城|曼彻斯特城
热刺|托特纳姆热刺
狼队|伍尔弗汉普顿
切尔滕汉姆|Cheltenham
埃克塞特|Exeter|Exeter City
博尔顿|博尔顿
剑桥联|Cambridge United
斯蒂夫尼奇|Stevenage
雷克瑟姆|Wrexham
卡莱尔联|Carlisle
哈洛盖特|Harrogate
萨尔福德|Salford
诺茨郡|Notts County|Notts County
格里姆斯比|Grimsby
克劳利|Crawley
布拉德福德|Bradford
科尔切斯特|Colchester
莫克姆|Morecambe
特兰米尔|Tranmere
纽波特|Newport
斯温登|Swindon
唐卡斯特|Doncaster
AFC温布尔登|AFC Wimbledon
沃尔索尔|Walsall
维尔港|Port Vale
巴罗|Barrow
哈特尔浦|Hartlepool
奥尔德姆|Oldham
约维尔|Yeovil
曼斯菲尔德|Mansfield
克鲁|Crewe
吉灵厄姆|Gillingham
北安普敦|Northampton
切尔滕纳姆|Cheltenham
布伦特伍德|Brentwood
罗奇代尔|Rochdale
博雷汉姆伍德|Boreham Wood
绍森德|Southend
巴尼特|Barnet
多尔金|Dorking
菲尔德|Fylde
盖茨黑德|Gateshead
奥尔德肖特|Aldershot
约克|York
波士顿|Boston
斯卡伯勒|Scarborough
索利赫尔|Solihull
切斯特菲尔德|Chesterfield
诺茨郡|诺茨郡
雷丁|雷丁
温布尔登|Wimbledon
女王公园|女王公园巡游者
谢周三|Sheffield Wednesday
彼得堡|Peterborough
罗瑟汉姆|罗瑟勒姆
布拉德福德|布拉德福德
伯顿|Burton|Burton Albion
维尔港|维尔港
AFC温布尔登|AFC温布尔登
福利特伍德|Fleetwood
阿克灵顿|Accrington
韦尔港|Port Vale
莫克姆|莫克姆
卡莱尔|Carlisle
哈特尔浦|哈特尔浦
奥尔德姆|奥尔德姆
布拉德福德|布拉德福德
斯肯索普|Scunthorpe
切斯特菲尔德|切斯特菲尔德
约克城|York City
盖茨黑德|盖茨黑德
波士顿联|Boston United
阿尔特林厄姆|Altrincham
博尔顿|博尔顿
斯托克港|Stockport County
诺茨郡|诺茨郡
雷克斯汉姆|雷克斯汉姆
格里姆斯比|格里姆斯比
巴罗|巴罗

皇家马德里|Real Madrid
巴塞罗那|Barcelona
马德里竞技|Atletico Madrid|Atlético Madrid
塞维利亚|Sevilla|Sevilla FC
巴伦西亚|Valencia
比利亚雷亚尔|Villarreal
毕尔巴鄂竞技|Athletic Club|Athletic Bilbao
皇家社会|Real Sociedad
皇家贝蒂斯|Real Betis
拉科鲁尼亚|Deportivo|Deportivo La Coruna|Deportivo de A Coruna
西班牙人|Espanyol
赫塔费|Getafe
阿拉维斯|Alaves|Deportivo Alaves
赫罗纳|Girona
维戈塞尔塔|塞尔塔|Celta Vigo|Celta de Vigo
奥萨苏纳|Osasuna
马略卡|Mallorca|RCD Mallorca
埃尔切|Elche
莱万特|Levante
巴列卡诺|Rayo Vallecano
加的斯|Cadiz|Cádiz
格拉纳达|Granada
巴拉多利德|Valladolid|Real Valladolid
阿尔梅里亚|Almeria
拉斯帕尔马斯|Las Palmas
奥维耶多|Oviedo|Real Oviedo
桑坦德竞技|Racing Santander|Racing de Santander
埃瓦尔|Eibar
希洪竞技|Sporting Gijon
阿尔科孔|Alcorcon
特内里费|Tenerife
米兰德斯|Mirandes
布尔戈斯|Burgos
卡塔赫纳|Cartagena
卢戈|Lugo
蓬费拉迪纳|Ponferradina
萨拉戈萨|Zaragoza|Real Zaragoza
阿尔瓦塞特|Albacete
伊维萨|Ibiza
马拉加|Malaga
莱加内斯|Leganes
努曼西亚|Numancia
科尔多瓦|Cordoba
萨雷斯|Xerez
穆尔西亚|Murcia
卡斯特利翁|Castellon
费罗尔|Ferrol
桑坦德|桑坦德竞技
阿尔瓦塞特|阿尔瓦塞特

尤文图斯|Juventus
国际米兰|Inter|Inter Milan|Internazionale
AC米兰|AC Milan|Milan
那不勒斯|Napoli|Napoli FC
罗马|Roma|AS Roma
拉齐奥|Lazio
亚特兰大|Atalanta
佛罗伦萨|Fiorentina|Florence
博洛尼亚|Bologna
都灵|Torino|Torino FC
桑普多利亚|Sampdoria
乌迪内斯|Udinese
热那亚|Genoa
维罗纳|Verona|Hellas Verona|Hellas Verona
恩波利|Empoli
萨索洛|Sassuolo
卡利亚里|Cagliari
莱切|Lecce
蒙扎|Monza
科莫|Como
帕尔马|Parma
威尼斯|Venezia
克雷莫纳|Cremona
弗罗西诺内|Frosinone
萨勒尼塔纳|Salernitana
斯佩齐亚|Spezia
贝内文托|Benevento
莱切|莱切
佩鲁贾|Perugia
特尔纳纳|Ternana
阿斯科利|Ascoli
布雷西亚|Brescia
奇塔代拉|Cittadella
雷吉纳|Reggina
克罗托内|Crotone
皮亚琴察|Piacenza
诺瓦拉|Novara
卡尔皮|Carpi
普罗韦尔切利|Pro Vercelli
恩特拉|Entella
斯帕尔|Spal
弗洛西诺内|弗罗西诺内
巴里|Bari
帕勒莫|Palermo
卡塔尼亚|Catania
切沃|Chievo
利沃诺|Livorno
摩德纳|Modena
雷吉亚纳|Reggiana
佩斯卡拉|Pescara
阿韦利诺|Avellino
维琴察|Vicenza
科森扎|Cosenza
阿斯科利|阿斯科利
巴里|巴里
费拉尔皮萨洛|Feralpisalo
南蒂罗尔|Sudtirol
卡利亚里|卡利亚里
科莫|科莫
克雷莫纳|克雷莫纳
帕尔马|帕尔马
威尼斯|威尼斯
莱切|莱切
蒙扎|蒙扎
萨索洛|萨索洛
热那亚|热那亚
维罗纳|维罗纳
乌迪内斯|乌迪内斯
恩波利|恩波利
萨勒尼塔纳|萨勒尼塔纳
斯佩齐亚|斯佩齐亚
弗罗西诺内|弗罗西诺内
卡利亚里|卡利亚里

拜仁慕尼黑|Bayern Munich|Bayern München
多特蒙德|Borussia Dortmund|Dortmund
勒沃库森|Bayer Leverkusen|Leverkusen
RB莱比锡|RB Leipzig|Leipzig|RasenBallsport Leipzig
法兰克福|Eintracht Frankfurt|Frankfurt
门兴格拉德巴赫|Borussia M.Gladbach|Borussia Monchengladbach|Mönchengladbach|Gladbach
斯图加特|VfB Stuttgart|Stuttgart
沃尔夫斯堡|Wolfsburg
霍芬海姆|Hoffenheim
弗赖堡|Freiburg|SC Freiburg
美因茨|Mainz|Mainz 05
云达不莱梅|Werder Bremen|Bremen
奥格斯堡|Augsburg
柏林联合|Union Berlin
科隆|FC Koln|Köln|Cologne
波鸿|Bochum
海登海姆|Heidenheim
达姆施塔特|Darmstadt
沙尔克04|Schalke|Schalke 04
纽伦堡|Nurnberg|Nuremberg
凯泽斯劳滕|Kaiserslautern
圣保利|St. Pauli|Saint Pauli
汉堡|Hamburg|Hamburger SV
汉诺威96|Hannover|Hannover 96
杜塞尔多夫|Dusseldorf|Fortuna Dusseldorf
卡尔斯鲁厄|Karlsruhe
帕德博恩|Paderborn
比勒费尔德|Bielefeld|Arminia Bielefeld
雷根斯堡|Regensburg
菲尔特|Furth|Greuther Furth
桑德豪森|Sandhausen
奥斯纳布吕克|Osnabruck|Osnabrück
海登海姆|海登海姆
杜伊斯堡|Duisburg
德累斯顿|Dinamo Dresden|Dresden
慕尼黑1860|Munich 1860|1860 Munich
维尔茨堡|Wurzburg
马格德堡|Magdeburg
布伦瑞克|Braunschweig|Eintracht Braunschweig
因戈施塔特|Ingolstadt
荷尔斯泰因基尔|Holstein Kiel|Kiel
明斯特|Munster|Preussen Munster
柏林赫塔|Hertha|Hertha Berlin
罗斯托克|Rostock
埃森|Essen
维多利亚|Viktoria
韦恩|Wehen|Wehen Wiesbaden
瓦尔多夫|Waldhof|Waldhof Mannheim
萨尔布吕肯|Saarbrucken|Saarbrücken
乌尔姆|Ulm
翁特哈兴|Unterhaching
阿伦|Aalen
德雷斯顿|Dresden

巴黎圣日耳曼|Paris Saint Germain|Paris Saint-Germain|PSG
马赛|Marseille|Olympique Marseille
摩纳哥|Monaco|AS Monaco
里昂|Lyon|Olympique Lyon
里尔|Lille
雷恩|Rennes
尼斯|Nice
朗斯|Lens
蒙彼利埃|Montpellier
圣埃蒂安|Saint Etienne|St Etienne
南特|Nantes
斯特拉斯堡|Strasbourg
波尔多|Bordeaux
马赛|马赛
兰斯|Reims
图卢兹|Toulouse
昂热|Angers
布雷斯特|Brest
洛里昂|Lorient|Lorient FC
克莱蒙|Clermont
特鲁瓦|Troyes
欧塞尔|Auxerre|AJ Auxerre
勒阿弗尔|Le Havre
梅斯|Metz
甘冈|Guingamp
卡昂|Caen|SM Caen
瓦朗谢纳|Valenciennes
阿雅克肖|Ajaccio
尼姆|Nimes
第戎|Dijon
索肖|Sochaux
勒芒|Le Mans
沙托鲁|Chateauroux
布尔日|Bourges
尼奥尔|Niort
罗德兹|Rodez
巴黎FC|Paris FC
奎维利|Quevilly
敦刻尔克|Dunkerque
波城|Pau
亚眠|Amiens
巴斯蒂亚|Bastia
卡昂|卡昂
第戎|第戎
索肖|索肖
勒芒|勒芒
梅斯|梅斯
南锡|Nancy
瓦朗谢讷|瓦朗谢讷
阿雅克肖|阿雅克肖
尼姆|尼姆
甘冈|甘冈
洛里昂|洛里昂
布雷斯特|布雷斯特
克莱蒙|克莱蒙
特鲁瓦|特鲁瓦
欧塞尔|欧塞尔
勒阿弗尔|勒阿弗尔
昂热|昂热
图卢兹|图卢兹
兰斯|兰斯
斯特拉斯堡|斯特拉斯堡
雷恩|雷恩
里尔|里尔
里昂|里昂
马赛|马赛
摩纳哥|摩纳哥
尼斯|尼斯
朗斯|朗斯
蒙彼利埃|蒙彼利埃
南特|南特
圣埃蒂安|圣埃蒂安

阿贾克斯|Ajax
埃因霍温|PSV|PSV Eindhoven
费耶诺德|Feyenoord
阿尔克马尔|AZ Alkmaar|AZ
海伦芬|Heerenveen
特温特|Twente|FC Twente
乌德勒支|FC Utrecht|Utrecht
维特斯|Vitesse
格罗宁根|FC Groningen|Groningen
鹿特丹斯巴达|Sparta Rotterdam
福图纳锡塔德|Fortuna Sittard
赫拉克勒斯|Heracles|Heracles Almelo
瓦尔韦克|RKC Waalwijk|Waalwijk
兹沃勒|PEC Zwolle|Zwolle
坎布尔|Cambuur|SC Cambuur
前进之鹰|Go Ahead Eagles
奈梅亨|NEC Nijmegen|NEC
阿尔梅勒城|Almere City
威廉二世|Willem II
SBV精英|Excelsior|SBV Excelsior
海牙|ADO Den Haag|ADo Den Haag|Den Haag
特尔斯达|Telstar
多德勒支|Dordrecht|FC Dordrecht
海尔蒙特|Helmond Sport
埃门|Emmen
格拉夫夏普|De Graafschap
奥斯|TOP Oss|Oss
登博思|Den Bosch|FC Den Bosch
罗达JC|Roda JC
马斯特里赫特|MVV Maastricht|MVV
福伦丹|Volendam
阿尔克马尔|阿尔克马尔
海伦芬|海伦芬
特温特|特温特
乌得勒支|乌得勒支
维特斯|维特斯
格罗宁根|格罗宁根
鹿特丹斯巴达|鹿特丹斯巴达
福图纳锡塔德|福图纳锡塔德
赫拉克勒斯|赫拉克勒斯
瓦尔韦克|瓦尔韦克
兹沃勒|兹沃勒
坎布尔|坎布尔
前进之鹰|前进之鹰
奈梅亨|奈梅亨
阿尔梅勒城|阿尔梅勒城
威廉二世|威廉二世
SBV精英|SBV精英
海牙|海牙
特尔斯达|特尔斯达
多德勒支|多德勒支
海尔蒙特|海尔蒙特
埃门|埃门
格拉夫夏普|格拉夫夏普
奥斯|奥斯
登博思|登博思
罗达JC|罗达JC
马斯特里赫特|马斯特里赫特
福伦丹|福伦丹

本菲卡|Benfica
波尔图|Porto|FC Porto
里斯本竞技|Sporting CP|Sporting Lisbon|Sporting
布拉加|Braga|SC Braga
吉马良斯|Vitoria de Guimaraes|Vitoria Guimaraes|Vitoria SC
里奥阿维|Rio Ave
圣克拉拉|Santa Clara
吉维森特|Gil Vicente
法马利康| Famalicao|FC Famalicao|Famalicao
阿罗卡|Arouca|FC Arouca
卡萨皮亚|Casa Pia
埃斯托里尔|Estoril|Estoril Praia
阿马多拉|Estrela da Amadora|Estrela Amadora
博阿维斯塔|Boavista
比兰伦斯|Belenenses
马里迪莫|Maritimo
费伦斯|Feirense
塞图巴尔|Setubal|Vitoria Setubal
通德拉|Tondela
莫雷拉人|Moreirense
佩纳菲尔|Penafiel
波尔蒂芒人|Portimonense
朴迪莫伦斯|Portimonense
维泽拉|Vizela
沙维什|Chaves
费雷拉|Paços de Ferreira|Pacos de Ferreira
科英布拉大学|Academica|Coimbra
国民队|Nacional|CD Nacional
马德拉国民|Nacional
莱里亚|Leiria|Uniao de Leiria
奥利维伦斯|Oliveirense
本菲卡|本菲卡
波尔图|波尔图
里斯本竞技|里斯本竞技
布拉加|布拉加
吉马良斯|吉马良斯
里奥阿维|里奥阿维
圣克拉拉|圣克拉拉
吉维森特|吉维森特
法马利康|法马利康
阿罗卡|阿罗卡
卡萨皮亚|卡萨皮亚
埃斯托里尔|埃斯托里尔
阿马多拉|阿马多拉
博阿维斯塔|博阿维斯塔
比兰伦斯|比兰伦斯
马里迪莫|马里迪莫
费伦斯|费伦斯
塞图巴尔|塞图巴尔
通德拉|通德拉
莫雷拉人|莫雷拉人
佩纳菲尔|佩纳菲尔
波尔蒂芒人|波尔蒂芒人
维泽拉|维泽拉
沙维什|沙维什
费雷拉|费雷拉
科英布拉大学|科英布拉大学
国民队|国民队
莱里亚|莱里亚
奥利维伦斯|奥利维伦斯
阿维斯|Avs|AVS Futebol
维拉维索萨|Vilaverdense

布鲁日|Club Brugge|Blue Bridage
安德莱赫特|Anderlecht
安特卫普|Royal Antwerp|Antwerp
根特|Gent|AA Gent
亨克|Genk
标准列日|Standard Liege|Standard
沙勒罗瓦|Sporting Charleroi|Charleroi
梅赫伦|KV Mechelen|Mechelen
圣特吕登|St.Truiden|Sint-Truiden
奥哈瓦里|OH Leuven|Oud-Heverlee Leuven
韦斯特洛|Westerlo
圣吉罗斯联|Union St.Gilloise|Union SG|Union Saint-Gilloise
科特赖克|Kortrijk
色格拉布鲁日|Cercle Brugge
登德|Dender
贝弗伦|SK Beveren|Beveren
洛克伦|Lokeren
奥斯坦德|Oostende
瓦勒海姆|Waregem|Zulte Waregem
穆斯克龙|Mouscron
阿尔斯特|Aalst
蒂比兹|Tubize
利尔斯|Lierse
安特卫普|安特卫普
布鲁日|布鲁日
安德莱赫特|安德莱赫特
根特|根特
亨克|亨克
标准列日|标准列日
沙勒罗瓦|沙勒罗瓦
梅赫伦|梅赫伦
圣特吕登|圣特吕罗登
奥哈瓦里|奥哈瓦里
韦斯特洛|韦斯特洛
圣吉罗斯联|圣吉罗斯联
科特赖克|科特赖克
色格拉布鲁日|色格拉布鲁日
登德|登德
贝弗伦|贝弗伦

加拉塔萨雷|Galatasaray
费内巴切|Fenerbahce
贝西克塔斯|Besiktas
特拉布宗体育|Trabzonspor
巴沙克城|Istanbul Basaksehir|Basaksehir|Istanbul BB
卡斯帕萨|Kasimpasa
锡瓦斯体育|Sivasspor
阿兰亚体育|Alanyaspor
科尼亚体育|Konyaspor
卡拉古拉克|Fatih Karagumruk|Karagumruk
加济安泰普|Gaziantep|Gaziantep FK
里泽体育|Rizespor
安塔利亚体育|Antalyaspor
开塞利体育|Kayserispor
戈兹泰佩|Goztepe
埃于普体育|Eyupspor|Eyupsport
萨姆松体育|Samsunspor
波卢斯堡|Boluspor
阿达纳体育|Adana Demirspor|Adana
阿达纳|Adana
代米尔体育|Adana Demirspor
哈塔伊体育|Hatayspor
科贾埃利体育|Kocaelispor
伊斯坦布尔体育|Istanbulspor
皮里斯坦体育|Pendikspor
乌姆拉尼耶|Umraniyespor
梅尔辛|Mersin
奥尔杜体育|Ordu
埃尔祖鲁姆|Erzurumspor
阿克希萨尔|akhisarspor
根克勒比利吉|Genclerbirligi
安卡拉古库|Ankaragucu
埃斯基谢希尔|Eskisehirspor
马尼萨体育|Manisaspor
伊约普|伊尤普
萨姆松|萨姆松体育

凯尔特人|Celtic
格拉斯哥流浪者|Rangers|Glasgow Rangers
阿伯丁|Aberdeen
哈茨|Hearts|Heart of Midlothian
希伯尼安|Hibernian|Hibs
圣约翰斯顿|St. Johnstone|Saint Johnstone
马瑟韦尔|Motherwell
基尔马诺克|Kilmarnock
罗斯郡|Ross County
圣米伦|St Mirren|Saint Mirren
利文斯顿|Livingston
邓迪|Dundee
邓迪联|Dundee United
帕尔蒂克|Partick Thistle
因弗内斯|Inverness
哈密尔顿|Hamilton
福尔柯克|Falkirk
阿洛厄|Alloa
艾尔联|Ayr United
拉茨流浪者|Raith Rovers
阿布罗斯|Arbroath
格林诺克|Greenock Morton
皇后公园|Queen's Park|Queens Park
科夫流浪者|Cove Rangers
爱丁堡城|Edinburgh City
斯坦豪斯缪尔|Stenhousemuir
东法夫|East Fife
彼得黑德|Peterhead
蒙特罗斯|Montrose
斯塔诺克|Stranraer
邓巴顿|Dumbarton
克莱德|Clyde
斯特灵|Stirling Albion
贝里克|Berwick
安南|Annan Athletic
埃尔金城|Elgin City
布里金|Brechin
哥顿|Forfar
凯尔特人|凯尔特人
流浪者|格拉斯哥流浪者
流浪者|Rangers

哥本哈根|FC Copenhagen|Copenhagen
布隆德比|Brondby|Brondby IF
中日德兰|Midtjylland|FC Midtjylland
北西兰|Nordsjaelland|Nordsjælland
奥胡斯|Aarhus|AGF
兰德斯|Randers
欧登塞|Odense|OB
维堡|Viborg
锡尔克堡|Silkeborg
霍尔森斯|Horsens
维积利|Vejle|Vejle BK
灵比|Lyngby
赫尔辛格|Helsingor
哈维德夫|Hvidovre
瓦埃勒|Vejle
桑德捷斯基|Sonderjysk|Sonnerjyske
拜斯|Baerum? 
罗斯基勒|Roskilde
纳斯特维德|Naestved
霍尔拜克|Holbaek
科治|Koge
弗雷德里西亚|Fredericia
埃斯比约|Esbjerg
哥本哈根|哥本哈根
布隆德比|布隆德比
中日德兰|中日德兰
北西兰|北西兰
奥胡斯|奥胡斯
兰德斯|兰德斯
欧登塞|欧登塞
维堡|维堡
锡尔克堡|锡尔克堡
霍尔森斯|霍尔森斯
桑德捷斯基|桑德捷斯基

马尔默|Malmo|Malmö FF
AIK索尔纳|AIK|AIK Stockholm
佐加顿斯|Djurgarden|Djurgårdens
哈马比|Hammarby
诺尔雪平|Norrkoping|IFK Norrköping
埃尔夫斯堡|Elfsborg|IF Elfsborg
赫根|Hacken|BK Hacken
卡尔马|Kalmar
赫尔辛堡|Helsingborg
    米亚尔比|Mjallby|Mjällby
    瓦尔贝里|Varbergs|Varberg
    国际图尔库|Inter Turku|FC Inter Turku
    库奥皮奥|KuPS|Kuopion Palloseura
代格福什|Degerfors
哈尔姆斯塔德|Halmstad
天狼星|Sirius|IK Sirius
韦斯特罗斯|Vasteras|Västerås
布拉格|布拉格
哥德堡盖斯|GAIS|Gais
韦纳穆|Varnamo|Värnamo
奥斯达|Osters|Östers
兰斯克鲁纳|Landskrona
特雷勒堡|Trelleborg
延雪平|Jönköpings|Jönköpings Södra
松兹瓦尔|Sundsvall
奥尔格里特|Örgryte|Orgryte
法尔肯贝里|Falkenberg
阿西里斯卡|Assyriska
叙夫什堡|Syrianska
布洛马波卡纳|Brommapojkarna|Bp
于戈尔登|Djurgarden
佐加顿斯|佐加顿斯
哈马比|哈马比
诺尔雪平|诺尔雪平
埃尔夫斯堡|埃尔夫斯堡
赫根|赫根
卡尔马|卡尔马
赫尔辛堡|赫尔辛堡
米亚尔比|米亚尔比
瓦尔贝里|瓦尔贝里
代格福什|代格福什
哈尔姆斯塔德|哈尔姆斯塔德
天狼星|天狼星
韦斯特罗斯|韦斯特罗斯
哥德堡盖斯|哥德堡盖斯
韦纳穆|韦纳穆
奥斯达|奥斯达
兰斯克鲁纳|兰斯克鲁纳
特雷勒堡|特雷勒堡

罗森博格|Rosenborg
莫尔德|Molde
博多格林特|Bodo Glimt|Bodø Glimt
布兰|Bran
维京|Viking
瓦勒伦加|Valerenga|Vålerenga
利勒斯特罗姆|Lillestrom|Lillestrøm
萨普斯堡|Sarpsborg|Sarpsborg 08
斯特罗姆加斯特|Stromsgodset|Strømsgodset
海于格松|Haugesund
特罗姆瑟|Tromso|Tromsø
克里斯蒂安松|Kristiansund
奥德|Odds|Odd Grenland
桑纳菲尤尔|Sandefjord
萨尔普斯堡|萨尔普斯堡
姆约恩达伦|Mjondalen
康斯文格|Kongsvinger
腓特烈斯塔|Fredrikstad
奥斯陆KFUM|KFUM Oslo|Oslo KFUM
斯塔贝克|Stabek|Stabæk
松达尔|Sogndal
兰黑姆|Ranheim
阿克灵|Åsane
桑德尼斯|Sandnes Ulf
布莱尼|Bryne
埃格松|Egersund
利云格尔|Levanger
诺霍斯|Raufoss
斯凯德|Skeid
格鲁德|Grorud
比亚森|Byåsen
广士云格|Kfum Oslo
罗森博格|罗森博格
莫尔德|莫尔德
博多格林特|博多格林特
布兰|布兰
维京|维京
瓦勒伦加|瓦勒伦加
利勒斯特罗姆|利勒斯特罗姆
斯特罗姆加斯特|斯特罗姆加斯特
海于格松|海于格松
特罗姆瑟|特罗姆瑟
克里斯蒂安松|克里斯蒂安松
奥德|奥德
桑纳菲尤尔|桑纳菲尤尔
腓特烈斯塔|腓特烈斯塔
奥斯陆KFUM|奥斯陆KFUM
斯塔贝克|斯塔贝克

雅典AEK|AEK Athens|AEK
奥林匹亚科斯|Olympiakos|Olympiacos
帕纳辛奈科|Panathinaikos
塞萨洛尼基|PAOK
阿里斯|Aris Thessaloniki|Aris
特里波利斯|Astana? |Asteras Tripolis
拉米亚|Lamia
沃洛斯|Volos
帕纳托利科斯|Panetolikos
阿特罗米托斯|Atromitos
OFI克里特|OFI Crete|OFI
伊奥尼科斯|Ionikos
克里特|克里特
卡利地亚|Kallithea
雅典AEK|雅典AEK
奥林匹亚科斯|奥林匹亚科斯
帕纳辛奈科|帕纳辛奈科
塞萨洛尼基|塞萨洛尼基
阿里斯|阿里斯
特里波利斯|Asteras Tripolis
拉米亚|拉米亚
沃洛斯|沃洛斯
帕纳托利科斯|帕纳托利科斯
阿特罗米托斯|阿特罗米托斯
OFI克里特|OFI克里特

维也纳快速|Rapid Vienna|Rapid Wien
萨尔茨堡红牛|Red Bull Salzburg|Salzburg
格拉茨风暴|Sturm Graz
奥地利维也纳|Austria Vienna|Austria Wien
林茨|LASK|Lask Linz
沃尔夫斯贝格|Wolfsberger|Wolfsberg
哈特贝格|Hartberg
阿尔塔奇|Altach
里德|Ried|SV Ried
维也纳|维也纳
阿德米拉|Admira
奥地利维也纳|奥地利维也纳
格拉茨风暴|格拉茨风暴
萨尔茨堡红牛|萨尔茨堡红牛
林茨|林茨
沃尔夫斯贝格|沃尔夫斯贝格
哈特贝格|哈特贝格
阿尔塔奇|阿尔塔奇
里德|里德

年轻人|Young Boys|YB
巴塞尔|Basel
卢加诺|Lugano
塞尔维特|Servette
圣加仑|St. Gallen|Saint Gallen
苏黎世|Zurich|FC Zurich
锡永|Sion
洛桑体育|Lausanne|Sport Lausanne
卢塞恩|Luzern
草蜢|Grasshopper|Grasshoppers
温特图尔|Winterthur
沙夫豪森|Schaffhausen
韦尔|Wil|FC Wil
阿劳|Aarau
纳沙泰尔|Neuchatel|Xamax
年轻人|年轻人
巴塞尔|巴塞尔
卢加诺|卢加诺
塞尔维特|塞尔维特
圣加仑|圣加仑
苏黎世|苏黎世
锡永|锡永
洛桑体育|洛桑体育
卢塞恩|卢塞恩
草蜢|草蜢
温特图尔|温特图尔

华沙莱吉亚|Legia Warsaw|Legia
波兹南莱赫|Lech Poznan|Lech
克拉科夫|Wisla Krakow|Wisla
扎布热|Zaglebie Lubin|Zagłębie Lubin
什切青|Lech? |Pogon Szczecin
比亚韦斯托克|Jagiellonia
格但斯克|Lechia Gdansk|Lechia
卢宾|Zaglebie
乔治罗尼亚|Jagiellonia
琴斯托霍瓦|Rakow Czestochowa|Rakow
普沃茨克|Wisla Plock
克拉科夫|克拉科夫
波兹南莱赫|波兹南莱赫
华沙莱吉亚|华沙莱吉亚
扎布热|扎布热
什切青|什切青
比亚韦斯托克|比亚韦斯托克
格但斯克|格但斯克
卢宾|卢宾
琴斯托霍瓦|琴斯托霍瓦
凯尔采|Korona Kielce
拉多姆|Radomiak
弗罗茨瓦夫|Slask Wroclaw|Slask
梅莱茨|Stal Mielec
涅波波曼|Nieciecza
格鲁琼兹|Arka Gdynia? 
列治亚|Lechia
特尔马利卡|Termalica
奥波莱|Odra Opole
斯卡利卡|Skalica
皮亚斯特|Piast Gliwice
卡托维采|GKS Katowice
彼得哥什|Zawisza

布拉格斯拉维亚|Slavia Prague|Slavia
布拉格斯巴达|Sparta Prague|Sparta
比尔森胜利|Viktoria Plzen|Plzen
布拉迪斯拉发|Slovan Bratislava|Slovan Bratislava
亚布洛内茨|Jablonec
奥洛穆茨|Sigma Olomouc
布拉格波希米亚|Bohemians 1905|Bohemians
利贝雷茨|Slovan Liberec|Liberec
奥斯特拉瓦|Banik Ostrava|Banik
特普利采|Teplice
帕尔杜比采|Pardubice
兹林|Zlin
卡尔维纳|Karvina
赫拉德茨|Hradec Kralove
布杰约维采|捷克布杰约维采|Ceske Budejovice
布拉格斯拉维亚|布拉格斯拉维亚
布拉格斯巴达|布拉格斯巴达
比尔森胜利|比尔森胜利
亚布洛内茨|亚布洛内茨
奥洛穆茨|奥洛穆茨
奥斯特拉瓦|奥斯特拉瓦
利贝雷茨|利贝雷茨
特普利采|特普利采
帕尔杜比采|帕尔杜比采
兹林|兹林
卡尔维纳|卡尔维纳
赫拉德茨|赫拉德茨

费伦茨瓦罗斯|Ferencvaros|Ferencváros
普斯卡什学院|Puskas Akademia
捍卫者|Honved|Kispest
乌伊佩斯特|Ujpest|Újpest
迪欧斯捷尔|Diósgyőr|Diosgyor
凯奇凯梅特|Kecskemet
保克什|Paks
萨拉格|Zalaegerszeg|ZTE
瓦萨斯|Vasas
MTK布达佩斯|MTK Budapest|MTK
德布雷岑|Debrecen
维迪顿|Vidi|MOL Fehervar|Fehervar
松博特海伊|Haladas
布达佩斯|布达佩斯
尼尔吉哈佐|Nyiregyhaza
斯洛瓦茨科|Slovacko
费伦茨瓦罗斯|费伦茨瓦罗斯
普斯卡什学院|普斯卡什学院
捍卫者|捍卫者
乌伊佩斯特|乌伊佩斯特
迪欧斯捷尔|迪欧斯捷尔
凯奇凯梅特|凯奇凯梅特
保克什|保克什
萨拉格|萨拉格
瓦萨斯|瓦萨斯
MTK布达佩斯|MTK布达佩斯
德布雷岑|德布雷岑
维迪顿|维迪顿

华盛顿联|DC United
辛辛那提|FC Cincinnati|Cincinnati
圣路易斯城|St. Louis City|St. Louis
洛杉矶银河|LA Galaxy|Galaxy
纽约红牛|New York Red Bulls|NY Red Bulls
纽约城|New York City|NYC FC
西雅图海湾人|Seattle Sounders|Seattle
波特兰伐木工|Portland Timbers|Portland
洛杉矶FC|Los Angeles FC|LAFC
多伦多FC|Toronto FC|Toronto
亚特兰大联|Atlanta United|Atlanta
国际迈阿密|Inter Miami|Miami
奥兰多城|Orlando City|Orlando
新英格兰革命|New England Revolution|New England
芝加哥火焰|Chicago Fire|Chicago
费城联合|Philadelphia Union|Philly
纳什维尔|Nashville SC|Nashville
奥斯汀|Austin FC|Austin
夏洛特|Charlotte FC|Charlotte
堪萨斯城竞技|Sporting Kansas City|KC
休斯顿迪纳摩|Houston Dynamo|Houston
达拉斯FC|FC Dallas|Dallas
皇家盐湖城|Real Salt Lake|Salt Lake
科罗拉多急流|Colorado Rapids|Colorado
圣何塞地震|San Jose Earthquakes|San Jose
温哥华白帽|Vancouver Whitecaps|Vancouver
蒙特利尔|CF Montreal|Montreal
明尼苏达联|Minnesota United|Minnesota
哥伦布机员|Columbus Crew|Columbus
蒙特利尔冲击|蒙特利尔
辛辛那提|辛辛那提
圣路易斯城|圣路易斯城
洛杉矶银河|洛杉矶银河
纽约红牛|纽约红牛
纽约城|纽约城
西雅图海湾人|西雅图海湾人
波特兰伐木工|波特兰伐木工
洛杉矶FC|洛杉矶FC
多伦多FC|多伦多FC
亚特兰大联|亚特兰大联
国际迈阿密|国际迈阿密
奥兰多城|奥兰多城
新英格兰革命|新英格兰革命
芝加哥火焰|芝加哥火焰
费城联合|费城联合
纳什维尔|纳什维尔
奥斯汀|奥斯汀
夏洛特|夏洛特
堪萨斯城竞技|堪萨斯城竞技
休斯顿迪纳摩|休斯顿迪纳摩
达拉斯FC|达拉斯FC
皇家盐湖城|皇家盐湖城
科罗拉多急流|科罗拉多急流
圣何塞地震|圣何塞地震
温哥华白帽|温哥华白帽
蒙特利尔|蒙特利尔
明尼苏达联|明尼苏达联
哥伦布机员|哥伦布机员

帕尔梅拉斯|Palmeiras
弗拉门戈|Flamengo
米内罗竞技|Atletico Mineiro|Atlético Mineiro
桑托斯|Santos
科林蒂安|Corinthians
圣保罗|Sao Paulo|São Paulo
格雷米奥|Gremio|Grêmio
国际|Internacional|SC Internacional
弗鲁米嫩塞|Fluminense
博塔弗戈|Botafogo
巴拉纳竞技|Atletico Paranaense|Athletico Paranaense
巴西国际|Internacional
科里蒂巴|Coritiba
克鲁塞罗|Cruzeiro
瓦斯科达伽马|Vasco da Gama|Vasco
巴伊亚|Bahia
库亚巴|Cuiaba
戈亚斯|Goias
福塔雷萨|Fortaleza
塞阿拉|Ceara|CEara
雷加塔斯|Atletico Goianiense|Atletico GO
阿瓦伊|Avaí|Avaí
庞特普雷塔|Ponte Preta
巴西瓜拉尼|Guarani
累西腓体育|Sport Recife
维多利亚|Vitoria|Vitória
尤文图德|Juventude
克里西乌马|Criciuma
布拉甘蒂诺|Red Bull Bragantino|Bragantino
米拉索尔|Mirassol
米内罗美洲|America MG|America Mineiro
戈亚尼亚竞技|Atletico Goianiense
沙佩科恩斯|Chapecoense
帕尔梅拉斯|帕尔梅拉斯
弗拉门戈|弗拉门戈
米内罗竞技|米内罗竞技
桑托斯|桑托斯
科林蒂安|科林蒂安
圣保罗|圣保罗
格雷米奥|格雷米奥
国际|国际
弗鲁米嫩塞|弗鲁米嫩塞
博塔弗戈|博塔弗戈
巴拉纳竞技|巴拉纳竞技
巴西国际|巴西国际
科里蒂巴|科里蒂巴
克鲁塞罗|克鲁塞罗
瓦斯科达伽马|瓦斯科达伽马
巴伊亚|巴伊亚
库亚巴|库亚巴
戈亚斯|戈亚斯
福塔雷萨|福塔雷萨
塞阿拉|塞阿拉
雷加塔斯|雷加塔斯
阿瓦伊|阿瓦伊
庞特普雷塔|庞特普雷塔
布拉甘蒂诺|布拉甘蒂诺
米拉索尔|米拉索尔
米内罗美洲|米内罗美洲
沙佩科恩斯|沙佩科恩斯
维多利亚|维多利亚
尤文图德|尤文图德
克里西乌马|克里西乌马

河床|River Plate
博卡青年|Boca Juniors
竞技|Racing Club|Racing
独立|Independiente
圣洛伦索|San Lorenzo
拉努斯|Lanus
萨斯菲尔德|Velez Sarsfield|Velez
胡拉坎|Huracan
戈多伊克鲁斯|Godoy Cruz
纽维尔老男孩|Newell's Old Boys|Newell's
罗萨里奥中央|Rosario Central
阿根廷青年人|Argentinos Juniors|Argentinos
图库曼竞技|Atletico Tucuman|Atletico Tucuman
普拉滕斯|Platense
科隆竞技|Colon|Colón
拉普拉塔大学生|Estudiantes|Estudiantes La Plata
拉普拉塔体操|Gimnasia La Plata|Gimnasia
班菲尔德|Banfield
防卫者|Defensa y Justicia
圣菲联合|Union de Santa Fe|Union
塔列雷斯|Talleres
贝尔格拉诺|Belgrano
巴拉卡斯中央|Barracas Central
萨尔米恩托|Sarmiento
图库曼|图库曼
阿尔多西维|Aldosivi
萨兰迪兵工厂|Arsenal de Sarandi|Arsenal Sarandi
查卡里塔|Chacarita
费罗卡利|Ferro Carril Oeste|Ferro
基尔梅斯|Quilmes
布朗海军|Brown de Adrogue
西部铁路|Ferro
河床|河床
博卡青年|博卡青年
竞技|竞技
独立|独立
圣洛伦索|圣洛伦索
拉努斯|拉努斯
萨斯菲尔德|萨斯菲尔德
胡拉坎|胡拉坎
戈多伊克鲁斯|戈多伊克鲁斯
纽维尔老男孩|纽维尔老男孩
罗萨里奥中央|罗萨里奥中央
阿根廷青年人|阿根廷青年人
普拉滕斯|普拉滕斯
科隆竞技|科隆竞技
拉普拉塔大学生|拉普拉塔大学生
拉普拉塔体操|拉普拉塔体操
班菲尔德|班菲尔德
防卫者|防卫者
圣菲联合|圣菲联合
塔列雷斯|塔列雷斯
贝尔格拉诺|贝尔格拉诺
巴拉卡斯中央|巴拉卡斯中央
萨尔米恩托|萨尔米恩托

解放者杯|Libertadores
南美杯|Copa Sudamericana|Sudamericana

利雅得新月|Al Hilal|Al-Hilal
利雅得胜利|Al Nassr|Al-Nassr
利雅得青年|Al Shabab|Al-Shabab
吉达国民|Al Ahli|Al-Ahli
吉达联合|Al Ittihad|Al-Ittihad
达曼协作|Al Ettifaq|Ettifaq
布赖代合作|Al Taawoun|Al-Taawoun
哈萨征服|Al Fateh|Al-Fateh
费哈|Al Faisaly|Al-Faisaly
布赖达|Al Raed|Al-Raed
麦加统一|Al Wehda|Al-Wehda
阿科多|Al Akhdoud
艾卜哈|Abha
卡利杰|Al Khaleej
哈森姆|Al Hazem
利雅得体育|Al Riyadh
欧鲁巴赫|Al Orobah
纳吉玛|Al Najma
新未来SC|Al Mujaddle? |Al Fayha? |Neom|新未来
利雅得新月|利雅得新月
利雅得胜利|利雅得胜利
利雅得青年|利雅得青年
吉达国民|吉达国民
吉达联合|吉达联合
达曼协作|达曼协作
布赖代合作|布赖代合作
哈萨征服|哈萨征服
费哈|费哈
布赖达|布赖达
麦加统一|麦加统一
阿科多|阿科多
艾卜哈|艾卜哈
卡利杰|卡利杰
哈森姆|哈森姆
利雅得体育|利雅得体育
欧鲁巴赫|欧鲁巴赫

川崎前锋|Kawasaki Frontale|Kawasaki
横滨水手|Yokohama F. Marinos|Yokohama Marinos
鹿岛鹿角|Kashima Antlers|Kashima
浦和红钻|Urawa Red Diamonds|Urawa
名古屋鲸八|Nagoya Grampus|Nagoya
大阪钢巴|Gamba Osaka|Gamba
神户胜利船|Vissel Kobe|Kobe
广岛三箭|Sanfrecce Hiroshima|Hiroshima
东京FC|FC Tokyo|Tokyo
大阪樱花|Cerezo Osaka|Cerezo
福冈黄蜂|Avispa Fukuoka|Fukuoka
札幌冈萨多|Hokkaido Consadole Sapporo|Sapporo
柏太阳神|Kashiwa Reysol|Kashiwa
湘南丽海|Shonan Bellmare|Shonan
清水心跳|Shimizu S-Pulse|Shimizu
京都不死鸟|Kyoto Sanga|Kyoto
鸟栖砂岩|Sagan Tosu|Sagan
新潟天鹅|Albirex Niigata|Niigata
磐城FC|Iwaki FC|Iwaki
东京绿茵|Tokyo Verdy|Verdy
千叶市原|JEF United Chiba|Chiba
德岛漩涡|Tokushima Vortis|Tokushima
山形山神|Montedio Yamagata|Yamagata
长崎航海|V-Varen Nagasaki|Nagasaki
大分三神|Oita Trinita|Oita
水户蜀葵|Mito HollyHock|Mito
群马温泉|Thespakusatsu Gunma|Gunma
秋田蓝色闪电|Blaublitz Akita|Akita
枥木SC|Tochigi SC|Tochigi
大宫松鼠|Omiya Ardija|Omiya
仙台七夕|Vegalta Sendai|Sendai
富山胜利|Toyama|Toyama
爱媛|Ehime
熊本深红|Roasso Kumamoto|Kumamoto
雷法山口|Renofa Yamaguchi|Yamaguchi
金泽|Zweigen Kanazawa|Kanazawa
藤枝MYFC|Fujieda MYFC|Fujieda
草津温泉|Thespa
甲府风林|Ventforet Kofu|Kofu
冈山绿雉|Fagiano Okayama|Okayama
山口|山口
川崎前锋|川崎前锋
横滨水手|横滨水手
鹿岛鹿角|鹿岛鹿角
浦和红钻|浦和红钻
名古屋鲸八|名古屋鲸八
大阪钢巴|大阪钢巴
神户胜利船|神户胜利船
广岛三箭|广岛三箭
东京FC|东京FC
大阪樱花|大阪樱花
福冈黄蜂|福冈黄蜂
札幌冈萨多|札幌冈萨多
柏太阳神|柏太阳神
湘南丽海|湘南丽海
清水心跳|清水心跳
京都不死鸟|京都不死鸟
鸟栖砂岩|鸟栖砂岩
新潟天鹅|新潟天鹅
磐城FC|磐城FC
东京绿茵|东京绿茵
千叶市原|千叶市原
德岛漩涡|德岛漩涡
山形山神|山形山神
长崎航海|长崎航海
大分三神|大分三神
水户蜀葵|水户蜀葵
群马温泉|群马温泉
秋田蓝色闪电|秋田蓝色闪电
枥木SC|枥木SC
大宫松鼠|大宫松鼠
仙台七夕|仙台七夕
富山胜利|富山胜利
爱媛|爱媛
熊本深红|熊本深红
雷法山口|雷法山口
金泽|金泽
藤枝MYFC|藤枝MYFC
甲府风林|甲府风林
冈山绿雉|冈山绿雉

全北现代|Jeonbuk Hyundai Motors|Jeonbuk
蔚山现代|Ulsan Hyundai|Ulsan
首尔FC|FC Seoul|Seoul
浦项制铁|Pohang Steelers|Pohang
水原三星|Suwon Samsung Bluewings|Suwon
仁川联|Incheon United|Incheon
大田市民|Daejeon Citizen|Daejeon
江原FC|Gangwon FC|Gangwon
济州SK|Jeju United|Jeju
光州FC|Gwangju FC|Gwangju
大邱FC|Daegu FC|Daegu
全南天龙|Jeonnam Dragons|Jeonnam
安养FC|FC Anyang|Anyang
金泉尚武|Gimcheon Sangmu|Gimcheon
水原FC|Suwon FC|Suwon FC
城南FC|Seongnam FC|Seongnam
釜山IPark|Busan IPark|Busan
富川|Bucheon
天安城|Chungnam Asan|Asan
庆南|Gyeongnam
安山小绿人|Ansan Greeners|Ansan
全北现代|全北现代
蔚山现代|蔚山现代
首尔FC|首尔FC
浦项制铁|浦项制铁
水原三星|水原三星
仁川联|仁川联
大田市民|大田市民
江原FC|江原FC
济州SK|济州SK
光州FC|光州FC
大邱FC|大邱FC
全南天龙|全南天龙
安养FC|安养FC
金泉尚武|金泉尚武
水原FC|水原FC
城南FC|城南FC
釜山IPark|釜山IPark
富川|富川
天安城|天安城
庆南|庆南
安山小绿人|安山小绿人

上海海港|Shanghai Port|Shanghai SIPG
上海申花|Shanghai Shenhua
北京国安|Beijing Guoan|Beijing
山东泰山|Shandong Taishan|Shandong
武汉三镇|Wuhan Three Towns|Wuhan
浙江队|Zhejiang
成都蓉城|Chengdu Rongcheng|Chengdu
河南队|Henan
天津津门虎|Tianjin Jinmen Tiger|Tianjin
长春亚泰|Changchun Yatai|Changchun
沧州雄狮|Cangzhou Mighty Lions|Cangzhou
梅州客家|Meizhou Hakka
深圳队|Shenzhen
大连人|Dalian Pro|Dalian
广州队|Guangzhou
重庆两江竞技|Chongqing
青岛海牛|Qingdao Hainiu|Qingdao
南通支云|Nantong Zhiyun|Nantong
昆山|Kunshan
陕西长安竞技|Shaanxi
石家庄功夫|Shijiazhuang
南京城市|Nanjing
苏州东吴|Suzhou
广西平果|Guangxi
四川九牛|Sichuan
黑龙江冰城|Heilongjiang
无锡吴钩|Wuxi
济南兴洲|Jinan
东莞莞联|Guangdong
上海嘉定汇龙|Shanghai Jiading
北京理工|Beijing IT
绍兴上虞|Shaoxing
丹东腾跃|Dandong
延边龙鼎|Yanbian
云南玉昆|Yunnan
辽宁铁人|Liaoning
重庆铜梁龙|Chongqing Tongliang
广州影豹|Guangzhou Eternal
中超|中超
上海海港|上海海港
上海申花|上海申花
北京国安|北京国安
山东泰山|山东泰山
武汉三镇|武汉三镇
浙江队|浙江队
成都蓉城|成都蓉城
河南队|河南队
天津津门虎|天津津门虎
长春亚泰|长春亚泰
沧州雄狮|沧州雄狮
梅州客家|梅州客家
深圳队|深圳队
大连人|大连人
广州队|广州队
青岛海牛|青岛海牛
南通支云|南通支云

墨尔本胜利|Melbourne Victory
墨尔本城|Melbourne City
悉尼FC|Sydney FC
西部联|Western United
阿德莱德联|Adelaide United|Adelaide
布里斯班狮吼|Brisbane Roar|Brisbane
中央海岸水手|Central Coast Mariners|Central Coast
珀斯光荣|Perth Glory|Perth
纽卡斯尔喷气机|Newcastle Jets|Newcastle Jets
惠灵顿凤凰|Wellington Phoenix|Wellington
麦克阿瑟|Macarthur
西悉尼流浪者|Western Sydney Wanderers|Western Sydney
墨尔本胜利|墨尔本胜利
墨尔本城|墨尔本城
悉尼FC|悉尼FC
西部联|西部联
阿德莱德联|阿德莱德联
布里斯班狮吼|布里斯班狮吼
中央海岸水手|中央海岸水手
珀斯光荣|珀斯光荣
纽卡斯尔喷气机|纽卡斯尔喷气机
惠灵顿凤凰|惠灵顿凤凰
麦克阿瑟|麦克阿瑟
西悉尼流浪者|西悉尼流浪者

特温特|特温特
海伦芬|海伦芬

欧冠|Champions League|UEFA Champions League
欧联|Europa League|UEFA Europa League
欧会杯|Conference League|UEFA Conference League
欧国联|Nations League|UEFA Nations League
世界杯|World Cup
亚洲杯|Asian Cup
欧洲杯|European Championship
美洲杯|Copa America
非洲杯|AFCON|Africa Cup of Nations
中超|Chinese Super League|CSL
英超|Premier League|EPL
英冠|Championship|EFL Championship
英甲|League One
英乙|League Two
西甲|LaLiga|La Liga
西乙|LaLiga2|Segunda Division
德甲|Bundesliga
德乙|2. Bundesliga
意甲|Serie A
意乙|Serie B
法甲|Ligue 1
法乙|Ligue 2
荷甲|Eredivisie
荷乙|Eerste Divisie|Keuken Kampioen Divisie
葡超|Liga Portugal|Primeira Liga
葡甲|Liga Portugal 2|Segunda Liga
比甲|Jupiler Pro League|Pro League
比乙|Challenger Pro League
土超|Süper Lig|Super Lig
俄超|Russian Premier League
苏超|Premiership|Scottish Premiership
丹超|Superliga|3F Superliga
芬超|Veikkausliiga|Veikkausliiga
瑞超|Allsvenskan
挪超|Eliteserien|Obos Liga? 
奥甲|Austrian Bundesliga
瑞士超|Swiss Super League
希腊超|Super League Greece|Greek Super League
波超|Ekstraklasa
捷甲|Chance Liga|Czech Liga
匈甲|NB I|OTP Bank Liga
克甲|Croatian Football League|HNL
塞超|Serbian SuperLiga
乌超|Ukrainian Premier League
罗甲|Liga I|Romanian Liga I
保甲|First Professional Football League|Parva Liga
斯洛文甲|PrvaLiga
斯洛伐克超|Niké Liga|Fortuna Liga
塞尔超|Serbian SuperLiga
阿甲|Primera División|Liga Profesional Argentina
巴甲|Campeonato Brasileiro Série A|Brasileirão
美职|Major League Soccer|MLS
美职联|Major League Soccer
日职|J1 League|Meiji Yasuda J1
日乙|J2 League
韩职|K League 1|K League
沙联|Saudi Pro League|Roshn Saudi League
澳超|A-League
解放者杯|Copa Libertadores
南美杯|Copa Sudamericana
亚冠|亚冠精英|AFC Champions League
亚冠精英|AFC Champions League Elite
亚冠二级|AFC Champions League Two
亚冠杯|AFC Champions League

国际赛|International
友谊赛|Friendly
国家队|National Team
# ==================== 补充映射（v1.7.22 数据驱动补齐） ====================
# 缺口来源：① whoscored_data.json 主流联赛未翻译队名；② history.json 竞彩
# 416 个历史队名中 128 个无英文映射。按「宁多勿漏」原则补齐，匹配要求
# home+away 双队名精确相等，多写条目不会造成误匹配。
# ---- 法甲/法乙（whoscored 覆盖法甲） ----
巴黎圣日耳曼|Paris Saint-Germain|PSG|Paris SG
巴黎圣日尔曼|Paris Saint-Germain|PSG
马赛|Marseille|Olympique Marseille|OM
里昂|Lyon|Olympique Lyonnais|OL
摩纳哥|Monaco|AS Monaco
里尔|Lille|LOSC Lille|Lille OSC
尼斯|Nice|OGC Nice
雷恩|Rennes|Stade Rennais|Stade Rennais FC
朗斯|Lens|RC Lens
斯特拉斯堡|Strasbourg|RC Strasbourg|Strasbourg Alsace
南特|Nantes|FC Nantes
图卢兹|Toulouse|Toulouse FC
布雷斯特|Brest|Stade Brestois|Stade Breton
兰斯|Reims|Stade de Reims
勒阿弗尔|Le Havre|Havre AC
欧塞尔|Auxerre|AJ Auxerre
昂热|Angers|Angers SCO
巴黎FC|Paris FC|Paris Football Club
洛里昂|Lorient|FC Lorient
蒙彼利埃|Montpellier|Montpellier HSC|HSC Montpellier
梅斯|Metz|FC Metz
圣埃蒂安|Saint-Etienne|AS Saint-Etienne|ASSE
罗德兹|Rodez|Rodez AF
敦刻尔克|Dunkerque|USL Dunkerque
克莱蒙|Clermont|Clermont Foot
甘冈|Guingamp|EA Guingamp
卡昂|Caen|SM Caen|Stade Malherbe Caen
阿讷西|Annecy|FC Annecy
拉瓦勒|Laval|Stade Lavallois
波城|Pau|Pau FC
巴斯蒂亚|Bastia|SC Bastia
格勒诺布尔|Grenoble|Grenoble Foot 38
特鲁瓦|Troyes|ESTAC Troyes
瓦朗谢讷|Valenciennes|VA FC
索肖|Sochaux|FC Sochaux
尼奥特|Niort|Chamois Niortais
阿雅克肖|Ajaccio|AC Ajaccio|GFCO Ajaccio
# ---- 德甲/德乙（whoscored 覆盖） ----
云达不来梅|Werder Bremen|Bremen|SV Werder Bremen
奥格斯堡|Augsburg|FC Augsburg
美因茨|Mainz|Mainz 05|FSV Mainz 05
沃尔夫斯堡|Wolfsburg|VfL Wolfsburg
弗赖堡|Freiburg|SC Freiburg
圣保利|St. Pauli|FC St Pauli|St Pauli
汉堡|Hamburg|Hamburger SV|HSV
基尔|Holstein Kiel|Kiel
海登海姆|Heidenheim|FC Heidenheim|1. FC Heidenheim
波鸿|Bochum|VfL Bochum
柏林联合|Union Berlin|FC Union Berlin|Union Berlin
门兴格拉德巴赫|Borussia M.Gladbach|Borussia Monchengladbach|Gladbach|Borussia MG
埃尔沃斯堡|Elversberg|SV Elversberg
沙尔克04|Schalke|Schalke 04|FC Schalke 04
凯泽斯劳滕|Kaiserslautern|1. FC Kaiserslautern
汉诺威96|Hannover|Hannover 96|Hannover 96
纽伦堡|Nurnberg|1. FC Nurnberg|Nuremberg
菲尔特|Greuther Furth|SpVgg Greuther Furth|Furth
卡尔斯鲁厄|Karlsruhe|Karlsruher SC
马格德堡|Magdeburg|1. FC Magdeburg
帕德博恩|Paderborn|SC Paderborn|Paderborn 07
柏林赫塔|Hertha Berlin|Hertha BSC|Hertha
杜塞尔多夫|Dusseldorf|Fortuna Dusseldorf|Dusseldorf
罗斯托克|Hansa Rostock|FC Hansa Rostock
因戈尔施塔特|Ingolstadt|FC Ingolstadt
雷根斯堡|Regensburg|Jahn Regensburg|SSV Jahn Regensburg
奥斯纳布吕克|Osnabruck|VfL Osnabruck
布伦瑞克|Braunschweig|Eintracht Braunschweig
# ---- 英冠/英甲（whoscored 覆盖 Championship） ----
利兹联|Leeds|Leeds United|Leeds Utd
桑德兰|Sunderland|Sunderland AFC
考文垂|Coventry|Coventry City
谢菲尔德联|Sheffield United|Sheff Utd|Sheffield Utd
谢菲尔德星期三|Sheffield Wednesday|Sheff Wed
诺维奇|Norwich|Norwich City
沃特福德|Watford|Watford FC
西布罗姆维奇|West Brom|West Bromwich Albion|WBA
米德尔斯堡|Middlesbrough|Middlesboro|Boro
布莱克本|Blackburn|Blackburn Rovers|Rovers
斯旺西|Swansea|Swansea City|Swans
米尔沃尔|Millwall|Millwall FC
普雷斯顿|Preston|Preston North End|PNE
布里斯托尔城|Bristol City|Bristol C
赫尔城|Hull|Hull City|Hull City AFC
斯托克城|Stoke|Stoke City|Potters
女王公园巡游者|QPR|Queens Park Rangers
普利茅斯|Plymouth|Plymouth Argyle
牛津联|Oxford|Oxford United
德比郡|Derby|Derby County|Rams
朴次茅斯|Portsmouth|Portsmouth FC
伊普斯维奇|Ipswich|Ipswich Town|Tractor Boys
莱斯特城|Leicester|Leicester City|Foxes
南安普顿|Southampton|Southampton FC|Saints
加的夫城|Cardiff|Cardiff City|Bluebirds
维冈竞技|Wigan|Wigan Athletic|Latics
罗瑟汉姆|Rotherham|Rotherham United
哈德斯菲尔德|Huddersfield|Huddersfield Town|Terriers
布莱克浦|Blackpool|Blackpool FC|Seasiders
彼得堡联|Peterborough|Peterborough United|POSH
博尔顿|Bolton|Bolton Wanderers|Trotters
切尔滕汉姆|Cheltenham|Cheltenham Town
弗利特伍德|Fleetwood|Fleetwood Town|Cod Army
什鲁斯伯里|Shrewsbury|Shrewsbury Town|Shrews
维尔港|Port Vale|PortVale
剑桥联|Cambridge|Cambridge United|U's
林肯城|Lincoln|Lincoln City|Imps
伯顿|Burton|Burton Albion|Brewers
埃克塞特城|Exeter|Exeter City|Grecians
雷丁|Reading|Reading FC|Royals
韦康比流浪者|Wycombe|Wycombe Wanderers|Chairboys
北安普顿|Northampton|Northampton Town|Cobblers
巴恩斯利|Barnsley|Barnsley FC|Tykes
查尔顿|Charlton|Charlton Athletic|Addicks
莱顿东方|Leyton Orient|O's
斯蒂夫尼奇|Stevenage|Stevenage FC|Boro
斯托克港|Stockport|Stockport County|Hatters
克劳利|Crawley|Crawley Town|Reds
AFC温布尔登|AFC Wimbledon|Wimbledon
曼斯菲尔德|Mansfield|Mansfield Town|Stags
# ---- 苏超（whoscored 覆盖） ----
哈茨|Heart of Midlothian|Hearts|Hearts of Midlothian
希伯尼安|Hibernian|Hibs
马瑟韦尔|Motherwell|Well
邓迪联|Dundee United|Dundee Utd|Terrors
邓迪|Dundee|Dundee FC|Dark Blues
圣米伦|St Mirren|St. Mirren|Buddies
阿伯丁|Aberdeen|Aberdeen FC|Dons
基马诺克|Kilmarnock|Killie
罗斯郡|Ross County|Staggies
利文斯顿|Livingston|Livi
圣约翰斯通|St Johnstone|Saints（同南安普顿，双条目无害）
# ---- 比甲（whoscored 覆盖） ----
圣吉尔联合|Union Saint-Gilloise|Union SG|USG|Royale Union Saint-Gilloise
安德莱赫特|Anderlecht|RSC Anderlecht
根特|Gent|KAA Gent|La Gantoise
亨克|Genk|KRC Genk
标准列日|Standard Liege|Standard|SL16 FC
安特卫普|Antwerp|Royal Antwerp|Great Old
梅赫伦|Mechelen|KV Mechelen|Yellow-Red
沙勒鲁瓦|Charleroi|Sporting Charleroi|Zebras
韦斯特鲁|Westerlo|KVC Westerlo
奥德赫维里|OH Leuven|Oud-Heverlee Leuven
色格拉布鲁日|Cercle Brugge|Cercle
科特赖克|Kortrijk|KV Kortrijk
欧本|Eupen|KAS Eupen
圣图尔登|Sint-Truiden|STVV|Sint Truiden
比尔肖特|Beerschot|K Beerschot VA
登德尔|Dender|FCV Dender EH|Dender EH
蒙斯|Mons|RAEC Mons
利亚斯|Lierse|Lierse SK
# ---- 荷甲（whoscored 覆盖） ----
PSV埃因霍温|PSV|PSV Eindhoven|Philips SV
费耶诺德|Feyenoord|Feyenoord Rotterdam
阿尔克马尔|AZ|AZ Alkmaar|Alkmaar
特温特|Twente|FC Twente
乌德勒支|Utrecht|FC Utrecht
乌得勒支|FC Utrecht
海伦芬|Heerenveen|SC Heerenveen
格罗宁根|Groningen|FC Groningen
奈梅亨|NEC|NEC Nijmegen|Nijmegen
福图纳锡塔德|Fortuna Sittard|Fortuna|Sittard
兹沃勒|PEC Zwolle|Zwolle
前进之鹰|Go Ahead Eagles|Go Ahead|Eagles
瓦尔韦克|RKC Waalwijk|Waalwijk
阿尔梅勒城|Almere City|Almere
鹿特丹斯巴达|Sparta Rotterdam|Sparta
布雷达|NAC Breda|Breda|NAC
威廉二世|Willem II|Willem
特尔斯达|Telstar|SC Telstar
sbv精英|Excelsior|SBV Excelsior|Rotterdam Excelsior
多德勒支|Dordrecht|FC Dordrecht
邓伯什|Den Bosch|FC Den Bosch
埃因霍温青年|Jong PSV|PSV U21（同 PSV，双条目无害）
# ---- 瑞超/瑞甲（whoscored 部分覆盖） ----
马尔默|Malmo|Malmo FF|Malmö FF
哈马比|Hammarby|Hammarby IF|Hammarby Fotboll
AIK索尔纳|AIK|AIK Solna|AIK Fotboll
埃尔夫斯堡|Elfsborg|IF Elfsborg
IFK哥德堡|IFK Goteborg|Goteborg|IFK Göteborg
赫根|Hacken|BK Hacken|Häcken
北雪平|Norrkoping|IFK Norrkoping|Norrköping
米亚尔比|Mjallby|Mjällby|IFK Mjallby
天狼星|Sirius|IK Sirius|Sirius FK
韦纳穆|Varnamo|IFK Varnamo|Värnamo
代格福什|Degerfors|Degerfors IF
卡尔马|Kalmar|Kalmar FF
哈尔姆斯塔德|Halmstads|Halmstad|Halmstads BK
奥斯达|Oster|Öster|Östers IF
哥德堡|GAIS|GAIS Goteborg|Gais
布鲁马波卡纳|Brommapojkarna|BP|Brommapojkarna IF
# ---- 挪超（whoscored 部分覆盖） ----
# v1.7.26：补 WhoScored 实际拼写变体 Bodoe/Glimt（周二004 曾因此失配）；
# 另注：博多格林特与博德闪耀为同一俱乐部两种译名，竞彩官方名为「博德闪耀」
博德闪耀|Bodo/Glimt|Bodo Glimt|FK Bodo/Glimt|Bodø/Glimt|Bodoe/Glimt|Bodø Glimt
莫尔德|Molde|Molde FK|Molde Fotball
维京|Viking|Viking FK|Viking Stavanger
布兰|Brann|SK Brann|Brann Bergen
罗森博格|Rosenborg|Rosenborg BK|RBK
特罗姆瑟|Tromso|Tromso IL|Tromsø IL
萨普斯堡|Sarpsborg|Sarpsborg 08|Sarpsborg 08 FF
斯托姆加斯特|Stromsgodset|Strømsgodset|Godset
汉坎|HamKam|Ham-Kam|Hamarkameratene
海于格松|Haugesund|FK Haugesund|FKH
奥德|Odd|Odds BK|Odd Grenland
克里斯蒂安松|Kristiansund|Kristiansund BK|KBK
瓦勒伦加|Valerenga|Vålerenga|Valerenga IF
奥勒松|Aalesund|Aalesunds FK|AaFK
松达尔|Sogndal|Sogndal Fotball
费德列斯达|Fredrikstad|Fredrikstad FK|FFK
KFUM奥斯陆|KFUM|KFUM Oslo|KFUM-Kameratene Oslo
布莱尼|Bryne|Bryne FK
桑讷菲尤尔|Sandefjord|Sandefjord Fotball|SF
# ---- 丹超（whoscored 部分覆盖） ----
哥本哈根|FC Copenhagen|Copenhagen|FCK
北西兰|FC Nordsjaelland
布隆德比|Brondby|Brondby IF|BIF
奥胡斯|AGF|Aarhus GF|Aarhus Gymnastikforening
兰讷斯|Randers|Randers FC|RFC
维堡|Viborg|Viborg FF|VFF
欧登塞|OB|Odense BK|Odense Boldklub
锡尔克堡|Silkeborg|Silkeborg IF|SIF
霍布罗|Hobro|Hobro IK
瓦埃勒|Vejle|Vejle BK|VB
桑德捷斯基|Sonderjyske|Sønderjyske|SonderjyskE
灵比|Lyngby|Lyngby BK|Kongens Lyngby
贺森斯|Horsens|AC Horsens|ACH
埃斯比约|Esbjerg|Esbjerg fB|EfB
纳斯特维德|Naestved|Næstved|Næstved BK
希勒勒|Hillerod|Hillerød|Hillerod Fodbold
# ---- 希超（whoscored 覆盖） ----
奥林匹亚科斯|Olympiacos|Olympiakos|Olympiacos Piraeus
帕纳辛奈科斯|Panathinaikos|PAO|Panathinaikos FC
塞萨洛尼基|PAOK|PAOK Thessaloniki|PAOK Saloniki
阿特罗米托斯|Atromitos|Atromitos Athens|Atromitos FC
帕尼奥尼奥斯|Panionios|Panionios Athens|Panionios FC
拉里萨|Larissa|AEL Larissa|AEL 1964
沃罗斯|Volos|NPS Volos|Volos NFC
帕纳托利克斯|Panetolikos|Panetolikos Agrinio
吉安尼纳|PAS Giannina|Giannina|PAS Giannina FC
莱瓦贾科斯|Levadiakos|Levadiakos FC
基亚沙|Kifisia|Kifisia FC|Asteras Kifisia
拉米亚|Lamia|PAS Lamia|Lamia FC
阿斯特拉斯特里波利斯|Asteras Tripolis|Asteras|Asteras Tripoli
克桑西|Xanthi|Xanthi FC|AO Xanthi
帕纳海基?不
# ---- 土超/土甲（whoscored 覆盖） ----
加拉塔萨雷|Galatasaray|GS|Galatasaray SK
费内巴切|Fenerbahce|Fener|Fenerbahce SK
贝西克塔斯|Besiktas|Besiktas JK|BJK
特拉布宗体育|Trabzonspor|TS|Trabzonspor SK
伊斯坦布尔巴萨克赛尔|Basaksehir|Istanbul Basaksehir|IBFK|Medipol Basaksehir
加济安泰普|Gaziantep|Gaziantep FK|GFK
科贾埃利|Kocaelispor|Kocaelispor SK
戈泽特佩|Goztepe|Goztepe SK|Göztepe
卡斯帕萨|Kasimpasa|Kasimpasa SK|Kasımpaşa
萨姆松体育|Samsunspor|Samsunspor SK
阿拉尼亚体育|Alanyaspor|Alanya|Alanyaspor SK
安塔利亚体育|Antalyaspor|Antalya|Antalyaspor SK
里泽体育|Rizespor|Caykur Rizespor|Rize
锡瓦斯体育|Sivasspor|Sivasspor SK|Sivas
开塞利体育|Kayserispor|Kayseri|Kayserispor SK
哈塔伊体育|Hatayspor|Hatay|Hatayspor SK
阿达纳代米尔|Adana Demirspor|Adana Demir|ADS
青年联合|Genclerbirligi|Gençlerbirliği|Genclerbirligi SK
埃尔祖鲁姆体育|Erzurumspor|Erzurumspor SK|Erzurum BB
波德鲁姆体育|Bodrumspor|Bodrum FK|Bodrum
伊斯坦布尔体育|Istanbulspor|Istanbulspor SK
乌姆拉尼耶|Umraniyespor|Umraniye|Umraniyespor SK
法蒂赫卡拉古鲁克|Karagumruk|Fatih Karagumruk|FK Fatih Karagumruk
阿克希萨尔|Akhisar|Akhisarspor|Akhisar Belediyespor
布尔萨体育|Bursaspor|Bursa|Bursaspor SK
# ---- 俄超/乌超（whoscored 覆盖部分） ----
莫斯科中央陆军|CSKA Moscow|CSKA Moskva|PFC CSKA
莫斯科斯巴达克|Spartak Moscow|Spartak Moskva|FC Spartak
莫斯科火车头|Lokomotiv Moscow|Loko Moscow|FC Lokomotiv
克拉斯诺达尔|Krasnodar|FC Krasnodar
泽尼特|Zenit|Zenit St Petersburg|Zenit SPb
喀山红宝石|Rubin Kazan|Rubin|FC Rubin Kazan
罗斯托夫|Rostov|FC Rostov
索契|Sochi|PFC Sochi
萨马拉苏维埃之翼|Krylya Sovetov|Krylia Sovetov Samara|Krylya Sovetov Samara
下诺夫哥罗德|Nizhny Novgorod|FC Nizhny Novgorod|Pari NN
马哈奇卡拉迪纳摩|Dynamo Makhachkala|Dinamo Makhachkala|FC Dynamo Makhachkala
格罗兹尼艾卡马特|Akhmat|Akhmat Grozny|Terek Grozny
乌拉尔|Ural|FC Ural|Ural Yekaterinburg
奥伦堡|Orenburg|FC Orenburg
沃罗涅日火炬|Fakel|Fakel Voronezh|FC Fakel
基辅迪纳摩|Dynamo Kyiv|Dinamo Kiev|FC Dynamo Kyiv
顿涅茨克矿工|Shakhtar|Shakhtar Donetsk|FC Shakhtar
利沃夫鲁克|Rukh|Rukh Lviv|FC Rukh Lviv
亚历山德里亚|Oleksandriya|FC Oleksandriya|Oleksandria
日托米尔波利西亚|Polissya|Polissya Zhytomyr|FC Polissya
卢甘斯克索尔亚|Zorya|Zorya Luhansk|FC Zorya
克拉夫巴斯|Kryvbas|Kryvbas Kryvyi Rih|Kryvbas KR
利沃夫喀尔巴阡|Karpaty|Karpaty Lviv|FC Karpaty
敖德萨黑海人|Chornomorets|Chornomorets Odesa|FC Chornomorets
文尼察|Inhulets|Inhulets Petrove|FC Inhulets
维勒斯|Veres|Veres Rivne|NK Veres
科洛什|Kolos|Kolos Kovalivka|FC Kolos
# ---- 美职联（whoscored 覆盖） ----
辛辛那提|FC Cincinnati|Cincinnati|FCC
哥伦布机员|Columbus Crew|Columbus|CLB
迈阿密国际|Inter Miami|Inter Miami CF|MIA
奥兰多城|Orlando City|Orlando City SC|ORL
纽约城|New York City FC|NYCFC|New York City
纽约红牛|New York Red Bulls|NY Red Bulls|RBNY
费城联合|Philadelphia Union|Philadelphia|PHI
新英格兰革命|New England Revolution|New England|NE
华盛顿联|D.C. United|DC United|DC
亚特兰大联|Atlanta United|Atlanta Utd|ATL
夏洛特|Charlotte FC|Charlotte|CLT
纳什维尔|Nashville SC|Nashville|NSH
芝加哥火焰|Chicago Fire|Chicago|CHI
多伦多|Toronto FC|Toronto|TOR
蒙特利尔|CF Montreal|Montreal|Montreal Impact|MTL
温哥华白帽|Vancouver Whitecaps|Vancouver|VAN
西雅图海湾人|Seattle Sounders|Seattle|SEA
波特兰伐木者|Portland Timbers|Portland|POR
洛杉矶银河|LA Galaxy|LA Galaxy|LAG
洛杉矶FC|Los Angeles FC|LAFC|LA
圣何塞地震|San Jose Earthquakes|San Jose|SJ
皇家盐湖城|Real Salt Lake|Salt Lake|RSL
科罗拉多急流|Colorado Rapids|Colorado|COL
明尼苏达联|Minnesota United|Minnesota|MIN
奥斯汀|Austin FC|Austin|ATX
休斯顿迪纳摩|Houston Dynamo|Houston|HOU
堪萨斯城竞技|Sporting Kansas City|Sporting KC|SKC
达拉斯|FC Dallas|Dallas|FCD
圣路易斯城|St. Louis City|St Louis CITY|STL
圣地亚哥|San Diego FC|San Diego|SD
# ---- 巴甲/阿甲/墨超（whoscored 覆盖巴甲） ----
科林蒂安|Corinthians|SC Corinthians|Corinthians Paulista
帕尔梅拉斯|Palmeiras|SE Palmeiras|Palmeiras SP
弗拉门戈|Flamengo|CR Flamengo|Flamengo RJ
弗鲁米嫩塞|Fluminense|Fluminense FC|FLU
瓦斯科达伽马|Vasco da Gama|Vasco|CR Vasco da Gama
博塔弗戈|Botafogo|Botafogo FR|Bota
桑托斯|Santos|Santos FC|Santos SP
圣保罗|Sao Paulo|São Paulo|Sao Paulo FC|SPFC
克鲁塞罗|Cruzeiro|EC Cruzeiro|Cruzeiro EC
米内罗竞技|Atletico Mineiro|Atlético Mineiro|Atletico-MG|CAM
格雷米奥|Gremio|Grêmio|Gremio FBPA|GRE
巴西国际|Internacional|SC Internacional|Inter de Porto Alegre
巴拉纳竞技|Athletico Paranaense|Athletico-PR|Athletico|CAP
尤文图德|Juventude|EC Juventude|Juventude RS
布拉干蒂诺RB|Red Bull Bragantino|Bragantino|RB Bragantino|RBB
库亚巴|Cuiaba|EC Cuiaba|Cuiabá
戈亚斯|Goias|EC Goias|Goiás EC
科里蒂巴|Coritiba|Coritiba FBC|CFC
维多利亚|Vitoria|EC Vitoria|Vitória
福塔莱萨|Fortaleza|EC Fortaleza|Fortaleza EC
塞阿拉|Ceara|Ceara SC|Ceará SC
巴伊亚|Bahia|EC Bahia|Bahia EC
累西腓体育|Sport Recife|Sport Club do Recife|Sport
克里丘马|Criciuma|Criciuma EC
诺瓦里桑蒂诺|Novorizontino|Novorizontino SP
戈亚尼恩斯竞技|Atletico Goianiense|Atletico-GO|ACG
庞特普雷塔|Ponte Preta|AA Ponte Preta
桑帕约科雷亚|Sampaio Correa|Sampaio Correa FC
瓜拉尼|Guarani|Guarani FC|Guarani Campinas
伊图阿诺|Ituano|Ituano FC|Ituano SP
欧帕瑞欧|Operario|Operario Ferroviario|Operario-PR
沙佩科恩斯|Chapecoense|Associacao Chapecoense|Chapecoense AF
博塔弗戈SP|Botafogo SP|Botafogo Futebol Clube
博卡青年|Boca Juniors|Boca|CA Boca Juniors
河床|River Plate|River|CA River Plate
竞技|Racing|Racing Club|Racing Avellaneda
独立|Independiente|CA Independiente|Independiente de Avellaneda
圣洛伦索|San Lorenzo|CASLA|San Lorenzo de Almagro
萨斯菲尔德|Velez|Velez Sarsfield|CA Velez
阿根廷青年人|Argentinos Juniors|Argentinos Jrs|AA Argentinos Juniors
拉普拉塔大学生|Estudiantes|Estudiantes LP|Estudiantes de La Plata
纽维尔老男孩|Newells|Newells Old Boys|Newell's Old Boys
班菲尔德|Banfield|CA Banfield
蒂格雷|Tigre|CA Tigre|Tigre de Victoria
防卫者|Defensa|Defensa y Justicia|Defensa y Justicia FC
萨兰迪兵工厂|Arsenal|Arsenal Sarandi|Arsenal de Sarandi
科尔多瓦学院|Instituto|Instituto ACC|Instituto de Cordoba
罗萨里奥中央|Rosario Central|CA Rosario Central|Canalla
科隆竞技|Colon|CA Colon|Colon de Santa Fe
拉努斯|Lanus|CA Lanus
塔勒瑞斯|Talleres|Talleres Cordoba|CAT
普拉腾斯|Platense|CA Platense
胡拉坎|Huracan|CA Huracan|Huracan de Parque Patricios
萨米恩托|Sarmiento|CA Sarmiento|Sarmiento de Junin
# ---- 日职/日乙（whoscored 覆盖部分） ----
横滨水手|Yokohama F. Marinos|Yokohama F Marinos|Yokohama Marinos|F Marinos
川崎前锋|Kawasaki Frontale|Kawasaki|Frontale
鹿岛鹿角|Kashima Antlers|Kashima|Antlers
浦和红钻|Urawa|Urawa Red Diamonds|Urawa Reds|Red Diamonds
大阪钢巴|Gamba Osaka|Gamba|Gamba
名古屋鲸八|Nagoya Grampus|Nagoya|Grampus
神户胜利船|Vissel Kobe|Vissel|Kobe
广岛三箭|Sanfrecce Hiroshima|Sanfrecce|Hiroshima
东京FC|FC Tokyo|Tokyo|FCTokyo
东京绿茵|Tokyo Verdy|Tokyo V|Verdy
清水鼓动|Shimizu S-Pulse|Shimizu|S-Pulse
磐田喜悦|Jubilo Iwata|Jubilo|Iwata
湘南海洋|Shonan Bellmare|Shonan|Bellmare
福冈黄蜂|Avispa Fukuoka|Avispa|Fukuoka
大阪樱花|Cerezo Osaka|Cerezo|Cerezo
京都不死鸟|Kyoto Sanga|Kyoto|Sanga
新泻天鹅|Albirex Niigata|Albirex|Niigata
柏雷素尔|Kashiwa Reysol|Kashiwa|Reysol
札幌冈萨多|Consadole Sapporo|Consadole|Sapporo
鸟栖砂岩|Sagan Tosu|Sagan|Tosu
町田泽维亚|Machida Zelvia|Machida|Zelvia
大宫松鼠RB|RB Omiya Ardija|Omiya Ardija|Omiya
山形山神|Montedio Yamagata|Montedio|Yamagata
甲府风林|Ventforet Kofu|Ventforet|Kofu
千叶市原|JEF United Chiba|JEF United|Chiba
德岛漩涡|Tokushima Vortis|Tokushima|Vortis
水户蜀葵|Mito Hollyhock|Mito|Hollyhock
枥木SC|Tochigi SC|Tochigi
秋田蓝闪电|Blaublitz Akita|Akita|Blaublitz
仙台七夕|Vegalta Sendai|Vegalta|Sendai
山口雷诺法|Renofa Yamaguchi|Renofa|Yamaguchi
冈山绿雉|Fagiano Okayama|Fagiano|Okayama
爱媛|Ehime FC|Ehime
藤枝MYFC|Fujieda MYFC|Fujieda
磐城|Iwaki FC|Iwaki
熊本深红|Roasso Kumamoto|Roasso|Kumamoto
长崎成功丸|V-Varen Nagasaki|V-Varen|Nagasaki
鹿儿岛联|Kagoshima United|Kagoshima
FC琉球|FC Ryukyu|Ryukyu
金泽塞维根|Zweigen Kanazawa|Zweigen|Kanazawa
富山胜利|Kataller Toyama|Kataller|Toyama
岐阜|FC Gifu|Gifu
群马草津温泉|Thespa Kusatsu Gunma|Thespakusatsu Gunma|Gunma
横滨FC|Yokohama FC|Yokohama
YSCC横滨|YSCC Yokohama|YSCC
松本山雅|Matsumoto Yamaga|Matsumoto
# ---- 韩K/韩K2（whoscored 覆盖部分） ----
蔚山现代|Ulsan Hyundai|Ulsan HD|Ulsan
全北现代|Jeonbuk|Jeonbuk Hyundai Motors|Jeonbuk Hyundai
浦项制铁|Pohang Steelers|Pohang|Steelers
首尔FC|FC Seoul|Seoul|Seoul FC
水原三星|Suwon Bluewings|Suwon Samsung|Bluewings
水原FC|Suwon FC
大邱|Daegu FC|Daegu
仁川联|Incheon United|Incheon
江原|Gangwon FC|Gangwon
济州联|Jeju United|Jeju SK|Jeju
光州|Gwangju FC|Gwangju
大田市民|Daejeon Hana Citizen|Daejeon|Hana Citizen
金泉尚武|Gimcheon Sangmu|Gimcheon|Sangmu
首尔衣恋|Seoul E-Land|Seoul Eland|E-Land
安养|Anyang FC|Anyang
富川FC|Bucheon FC 1995|Bucheon|Bucheon 1995
全南天龙|Jeonnam Dragons|Chunnam Dragons|Jeonnam
庆南|Gyeongnam FC|Gyeongnam
釜山IPark|Busan IPark|Busan|IPark
天安城|Cheonan City|Cheonan
华城|Hwaseong FC|Hwaseong
忠北清州|Chungbuk Cheongju|Cheongju
金浦|Gimpo FC|Gimpo
城南|Seongnam FC|Seongnam
牙山木槿花|Asan Mugunghwa|Asan
# ---- 澳超（whoscored 覆盖） ----
墨尔本胜利|Melbourne Victory|Melbourne Vic|Victory
墨尔本城|Melbourne City|Melb City|City
悉尼FC|Sydney FC|Sydney
西部联|Western United|Western Utd|WU
西悉尼流浪者|Western Sydney Wanderers|WSW|Wanderers
阿德莱德联|Adelaide United|Adelaide|Reds
布里斯班狮吼|Brisbane Roar|Brisbane|Roar
中央海岸水手|Central Coast Mariners|Central Coast|Mariners
纽卡斯尔喷气机|Newcastle Jets|Newcastle Jets|Jets
惠灵顿凤凰|Wellington Phoenix|Wellington|Phoenix
珀斯光荣|Perth Glory|Perth|Glory
麦克阿瑟|Macarthur FC|Macarthur|Bulls
奥克兰|Auckland FC|Auckland
# ---- 泰超/马来超/沙特联（whoscored 覆盖部分） ----
武里南联|Buriram United|Buriram|BRU
布里兰|Buriram United|Buriram
蒙通联|Muangthong United|Muangthong|MTU
曼谷联|Bangkok United|Bangkok Utd|BUFC
曼谷玻璃|BG Pathum United|BG Pathum|Pathum|BGPU
泰港|Port FC|Port|Thai Port
清莱联|Chiangrai United|Chiangrai|CRU
叻武里|Ratchaburi|Ratchaburi FC|RBM
巴吞联|PT Prachuap|Prachuap|PTPFC
那空拉查斯吗|Nakhon Ratchasima|NRM
素可泰|Sukhothai|Sukhothai FC
清迈联|Chiangmai United|Chiangmai|CMU
巴蜀基利|Police Tero|Tero|PT
春武里|Chonburi|Chonburi FC|CHO
坤敬|Khon Kaen United|Khon Kaen|KKU
柔佛DT|Johor Darul Takzim|JDT|Johor|Johor FC
雪兰莪|Selangor|Selangor FC|SEL
吉打|Kedah|Kedah Darul Aman|KDA
吉隆坡|Kuala Lumpur City|KL City|KL
槟城|Penang|Penang FA|PEN
丁加奴|Terengganu|Terengganu FC|TFC
森美兰|Negeri Sembilan|NSFC
沙巴|Sabah FC|Sabah|SAB
彭亨|Sri Pahang|Pahang|PAH
霹雳|Perak|Perak FC|PRK
马六甲联|Melaka United|Melaka|MEL
八打灵再也城|Petaling Jaya City|PJ City|PJC
皇家警察|PDRM|Polis Diraja Malaysia
玛拉工艺大学|UiTM|UiTM FC
利雅得新月|Al Hilal|Al-Hilal|HIL
利雅得胜利|Al Nassr|Al-Nassr|NAS
吉达联合|Al Ittihad|Ittihad|ITT
吉达阿赫利|Al Ahli|Al-Ahli|AHL
利雅得青年|Al Shabab|Al-Shabab|SHB
艾法特|Al Fateh|Al-Fateh|FAT
布赖代合作|Al Taawoun|Al Taee|Al-Taee|TAA
达曼协作|Al Ettifaq|Al-Ittifaq|Al Ittifaq|ETT
阿科多|Al Okhdood|Al-Okhdood|OKH
利雅得体育|Al Riyadh|Al-Riyadh|RIY
布赖代先锋|Al Raed|Al-Raed|RAE
麦加统一|Al Wehda|Al-Wehda|WHD
阿卜哈|Abha|Abha Club|ABH
塔伊|Al Tai|Al-Tai|TAI
# ---- 国家队/国际赛（whoscored 覆盖国际比赛日） ----
中国|China|China PR|Team China
中国亚运男足|China U23|China Olympic|China Asian Games
乌兹别克亚运男足|Uzbekistan U23|Uzbekistan Olympic
伊朗亚运男足|Iran U23|Iran Olympic
丹麦|Denmark|DEN|Danish
乌拉圭|Uruguay|URU|Uruguayan
加拿大|Canada|CAN|Canadian
巴西|Brazil|BRA|Brazilian
冰岛|Iceland|ISL|Icelandic
威尔士|Wales|WAL|Welsh
北爱尔兰|Northern Ireland|NIR|N Ireland
克罗地亚|Croatia|CRO|Croatian
塞尔维亚|Serbia|SRB|Serbian
匈牙利|Hungary|HUN|Hungarian
保加利亚|Bulgaria|BUL|Bulgarian
土耳其|Turkey|TUR|Turkiye|Turkish
奥地利|Austria|AUT|Austrian
瑞士|Switzerland|SUI|Swiss
卢森堡|Luxembourg|LUX|Luxembourgish
亚美尼亚|Armenia|ARM|Armenian
北马其顿|North Macedonia|MKD|Macedonia
乌兹别克斯坦|Uzbekistan|UZB|Uzbek
哈萨克斯坦|Kazakhstan|KAZ|Kazakh
塔吉克斯坦|Tajikistan|TJK|Tajik
委内瑞拉|Venezuela|VEN|Venezuelan
厄瓜多尔|Ecuador|ECU|Ecuadorian
卡塔尔|Qatar|QAT|Qatari
伊朗|Iran|IRN|Iran PR|Iranian
伊拉克|Iraq|IRQ|Iraqi
阿联酋|UAE|United Arab Emirates|ARE
阿曼|Oman|OMA|Omani
巴林|Bahrain|BHR|Bahraini
约旦|Jordan|JOR|Jordanian
黎巴嫩|Lebanon|LIB|Lebanese
叙利亚|Syria|SYR|Syrian
巴勒斯坦|Palestine|PLE|Palestinian
科威特|Kuwait|KUW|Kuwaiti
沙特阿拉伯|Saudi Arabia|KSA|Saudi|Green Falcons
也门|Yemen|YEM|Yemeni
越南|Vietnam|VIE|Vietnamese
泰国|Thailand|THA|Thai
马来西亚|Malaysia|MAS|Malaysian
印度尼西亚|Indonesia|IDN|Indonesian
菲律宾|Philippines|PHI|Philippine
新加坡|Singapore|SIN|Singaporean
缅甸|Myanmar|MYA|Myanma
柬埔寨|Cambodia|CAM|Cambodian
老挝|Laos|LAO|Laotian
印度|India|IND|Indian
马尔代夫|Maldives|MDV|Maldivian
孟加拉国|Bangladesh|BAN|Bangladeshi
尼泊尔|Nepal|NEP|Nepali
不丹|Bhutan|BHU|Bhutanese
斯里兰卡|Sri Lanka|SRI|Sri Lankan
巴基斯坦|Pakistan|PAK|Pakistani
阿富汗|Afghanistan|AFG|Afghan
蒙古|Mongolia|MNG|Mongolian
朝鲜|North Korea|PRK|Korea DPR|DPR Korea
韩国|South Korea|KOR|Korea Republic|Korea
日本|Japan|JPN|Japanese
澳大利亚|Australia|AUS|Australian|Socceroos
新西兰|New Zealand|NZL|All Whites
美国|United States|USA|USMNT|American
墨西哥|Mexico|MEX|Mexican|El Tri
哥斯达黎加|Costa Rica|CRC|Costa Rican
巴拿马|Panama|PAN|Panamanian
洪都拉斯|Honduras|HON|Honduran
萨尔瓦多|El Salvador|SLV|Salvadoran
危地马拉|Guatemala|GUA|Guatemalan
尼加拉瓜|Nicaragua|NCA|Nicaraguan
伯利兹|Belize|BLZ|Belizean
古巴|Cuba|CUB|Cuban
牙买加|Jamaica|JAM|Jamaican
海地|Haiti|HAI|Haitian
特立尼达和多巴哥|Trinidad and Tobago|TRI|Trinidad|Soca Warriors
苏里南|Suriname|SUR|Surinamese
圭亚那|Guyana|GUY|Guyanese
英格兰|England|ENG|English
苏格兰|Scotland|SCO|Scottish
爱尔兰|Republic of Ireland|Ireland|ROI|IRL
法国|France|FRA|French
德国|Germany|GER|German
意大利|Italy|ITA|Italian
西班牙|Spain|ESP|Spanish
葡萄牙|Portugal|POR|Portuguese
荷兰|Netherlands|NED|Holland|Dutch
比利时|Belgium|BEL|Belgian
波兰|Poland|POL|Polish
捷克|Czech Republic|Czechia|CZE|Czech
斯洛伐克|Slovakia|SVK|Slovak
罗马尼亚|Romania|ROU|Romanian
波黑|Bosnia and Herzegovina|Bosnia|BIH|Bosnian
黑山|Montenegro|MNE|Montenegrin
阿尔巴尼亚|Albania|ALB|Albanian
希腊|Greece|GRE|Greek
乌克兰|Ukraine|UKR|Ukrainian
俄罗斯|Russia|RUS|Russian
白俄罗斯|Belarus|BLR|Belarusian
立陶宛|Lithuania|LTU|Lithuanian
拉脱维亚|Latvia|LVA|Latvian
爱沙尼亚|Estonia|EST|Estonian
摩尔多瓦|Moldova|MDA|Moldovan
格鲁吉亚|Georgia|GEO|Georgian
阿塞拜疆|Azerbaijan|AZE|Azerbaijani
# 阿塞拜疆超俱乐部（v1.7.26 补：欧战常客；竞彩官方名「萨巴赫」。
# 注意与马来西亚球队「沙巴」(Sabah FC, 马来西亚区) 是两支不同球队，
# 这里只登记精确别名 Sabah FK，勿把沙巴改为萨巴赫）
萨巴赫|Sabah FK
以色列|Israel|ISR|Israeli
塞浦路斯|Cyprus|CYP|Cypriot
马耳他|Malta|MLT|Maltese
圣马力诺|San Marino|SMR|Sammarinese
安道尔|Andorra|AND|Andorran
列支敦士登|Liechtenstein|LIE|Liechtensteiner
法罗群岛|Faroe Islands|FRO|Faroe
科索沃|Kosovo|KOS|Kosovar
挪威|Norway|NOR|Norwegian
瑞典|Sweden|SWE|Swedish
芬兰|Finland|FIN|Finnish
# ---- 女足（whoscored 覆盖女足赛事） ----
中国女足|China Women|China W|China PR Women
美国女足|USA Women|United States Women|USWNT
日本女足|Japan Women|Japan W|Nadeshiko
英格兰女足|England Women|England W|Lionesses
德国女足|Germany Women|Germany W
西班牙女足|Spain Women|Spain W
法国女足|France Women|France W
巴西女足|Brazil Women|Brazil W
加拿大女足|Canada Women|Canada W
澳大利亚女足|Australia Women|Australia W|Matildas
瑞典女足|Sweden Women|Sweden W
荷兰女足|Netherlands Women|Netherlands W|Oranje Leeuwinnen
丹麦女足|Denmark Women|Denmark W
挪威女足|Norway Women|Norway W
韩国女足|South Korea Women|Korea Republic Women|Korea W
朝鲜女足|North Korea Women|Korea DPR Women
赞比亚女足|Zambia Women|Zambia W|Copper Queens
尼日利亚女足|Nigeria Women|Nigeria W|Super Falcons
南非女足|South Africa Women|South Africa W|Banyana
摩洛哥女足|Morocco Women|Morocco W|Atlas Lionesses
哥伦比亚女足|Colombia Women|Colombia W|Las Cafeteras
阿根廷女足|Argentina Women|Argentina W|Albicelestes
意大利女足|Italy Women|Italy W|Azzurre
葡萄牙女足|Portugal Women|Portugal W
瑞士女足|Switzerland Women|Switzerland W
新西兰女足|New Zealand Women|New Zealand W|Football Ferns
越南女足|Vietnam Women|Vietnam W
泰国女足|Thailand Women|Thailand W
菲律宾女足|Philippines Women|Philippines W
乌兹别克斯坦女足|Uzbekistan Women|Uzbekistan W
伊朗女足|Iran Women|Iran W|Team Melli Zanan
印度女足|India Women|India W
# ---- 北欧/东欧/西亚零星（history.json 缺口） ----
LASK林茨|LASK|LASK Linz|Linzer ASK
克拉约瓦大学|Universitatea Craiova|CS Universitatea Craiova|U Craiova
基多体育大学|LDU de Quito|LDU Quito|Liga de Quito|LDU
圣旺红星|Red Star|Red Star FC|Red Star Saint-Ouen|Red Star 93
奥莫尼亚|Omonia|Omonia Nicosia|AC Omonia
巴西雷加塔斯|Brasil de Pelotas|Brasil Pelotas|Xavante
竞赛联|Racing Union|Racing FC Union Luxembourg|RFCU
尼德康|FC Differdange 03|Differdange|FCD03
普罗格雷苏|Progres Niederkorn|FC Progres|Progres
斯威夫特|Swift Hesperange|FC Swift|Swift
阿诺索西斯|Anorthosis|Anorthosis Famagusta|Anorthosis FC
利马索尔太阳神|Apollon Limassol|Apollon|Apollon FC
AEL利马索尔|AEL Limassol|AEL|AEL FC
帕福斯|Paphos|Pafos FC|Pafos
卡托克匹亚斯|Doxa Katokopias|Doxa|Doxa FC
阿拉季普埃尔米斯|Ermis Aradippou|Ermis|Ermis FC
萨拉米斯|Nea Salamis|Nea Salamina|Nea Salamis Famagusta
阿西埃努|Ethnikos Achna|Ethnikos Achnas|EAS
AEK拉纳卡|AEK Larnaca|AEK|AEK Larnaca FC
# ---- 大洋洲/非洲零星（whoscored 有标签但竞彩少开，低优先级） ----
奥克兰城|Auckland City|Auckland City FC|ACFC
惠灵顿|Wellington Olympic|Wellington FC|WO
# ---- 补漏 v2：芬超/新加坡/日丙/厄瓜多尔/亚足U23/其余国家队（history.json 缺口） ----
赫尔辛基|HJK|HJK Helsinki|Helsingin Jalkapalloklubi
赫尔辛基火花|HIFK|HIFK Helsinki|Helsingfors IFK
瓦萨|VPS|VPS Vaasa|Vaasan Palloseura
AC奥卢|AC Oulu|Oulu
玛丽港|IFK Mariehamn|Mariehamn
科特布斯|Energie Cottbus|Cottbus|FC Energie Cottbus
淡宾尼士流浪|Tampines Rovers|Tampines
芽笼国际|Geylang International|Geylang
后港联|Hougang United|Hougang
菲律宾联军|United City FC|United City|Ceres Negros
内政联|Home United|Lion City Sailors|Lion City
新加坡新泻天鹅|Albirex Niigata Singapore|Albirex Singapore
枥木城|Tochigi City FC|Tochigi City
宫崎惊奇牛鸟|Tegevajaro Miyazaki|Miyazaki
德尔瓦耶独立|Independiente del Valle|Independiente DV|IDV
斯洛文尼亚|Slovenia|SVN|Slovenian
智利|Chile|CHI|Chilean
秘鲁|Peru|PER|Peruvian
卡塔尔亚足|Qatar U23|Qatar Olympic
日本亚足|Japan U23|Japan Olympic
沙特阿拉伯亚足|Saudi Arabia U23|Saudi U23
泰国亚运男足|Thailand U23|Thailand Olympic
越南亚运男足|Vietnam U23|Vietnam Olympic
朝鲜亚运男足|North Korea U23|Korea DPR U23
吉尔吉斯斯坦亚足|Kyrgyzstan U23|Kyrgyzstan Olympic
维迪斯|Vitesse|Vitesse Arnhem
萨尔茨堡|Red Bull Salzburg|Salzburg|FC Red Bull Salzburg|RB Salzburg
麦克阿瑟FC|Macarthur FC|Macarthur
阿布扎比艾因|Al Ain|Al-Ain|Al Ain FC
阿联酋亚运男足|UAE U23|United Arab Emirates U23
韩国亚运男足|South Korea U23|Korea Republic U23
采列|Celje|NK Celje

# v1.7.57：FD(football-data.org) 完整正式队名 → 规范中文（此前 spZh 仅覆盖常用短名，
# 导致 Atalanta BC / Bayer 04 Leverkusen / FC Internazionale Milano 等 35 个正式名未翻译→隐藏；
# 补全后 FD 返回的竞彩场次能以正确中文显示，不再因翻译失败被 spJcFilter 隐藏而遗漏）
科隆|1. FC Köln
柏林联合|1. FC Union Berlin
美因茨|1. FSV Mainz 05
亚特兰大|Atalanta BC
勒沃库森|Bayer 04 Leverkusen
门兴格拉德巴赫|Borussia Mönchengladbach
马德里竞技|Club Atlético de Madrid
阿拉维斯|Deportivo Alavés
热那亚|Genoa CFC
马拉加|Málaga CF
马赛|Olympique de Marseille
西班牙人|RCD Espanyol de Barcelona
朗斯|Racing Club de Lens
巴列卡诺|Rayo Vallecano de Madrid
皇家贝蒂斯|Real Betis Balompié
皇家社会|Real Sociedad de Fútbol
桑坦德竞技|Real Racing Club de Santander
国际米兰|FC Internazionale Milano
埃尔弗斯堡|SV 07 Elversberg
本菲卡|Sport Lisboa e Benfica
里斯本竞技|Sporting Clube de Portugal
布拉加|Sporting Clube de Braga
狼队|Wolverhampton Wanderers FC
特温特|FC Twente '65
威廉二世|Willem II Tilburg
斯旺西|Swansea City AFC
雷克瑟姆|Wrexham AFC
坎布尔|SC Cambuur-Leeuwarden
米内罗竞技|CA Mineiro
巴拉纳竞技|CA Paranaense
格雷米奥|Grêmio FBPA
雷莫|Clube do Remo
马德拉航海|CS Marítimo
法马利康|FC Famalicão
维塞乌学院|Académico de Viseu FC
"""

# ---- 构建归一化查表 ----
_CLUB_TOKENS = (
    "fc", "fk", "ac", "as", "us", "ud", "cd", "sk", "sv", "bk", "rc", "cf",
    "afc", "rsc", "sl", "sc", "ec", "tc", "kv", "krc", "nk", "hnk", "gnk",
    "ifk", "yb", "rb", "dj", "tsv", "vfb", "vfl", "fk", "ca", "cc", "gc",
    # 欧洲各语言「足球/俱乐部」通用词：WhoScored 常用全称（Parma Calcio 1913、
    # Futebol Clube do Porto 等），剥离后与映射表短名（Parma/Porto）对齐
    "calcio", "football", "club", "futebol", "futbol", "fotbal", "fotboll",
    "fodbold", "fussball", "voetbal", "klubb", "klub", "clube", "fudbalski",
)
# 姓名/队名中的虚词（仅作为独立 token 时剥离；"deportivo" 这类词不拆）
_FILLER_TOKENS = {
    "de", "del", "della", "di", "da", "do", "dos", "das", "of", "the", "and",
    "la", "le", "el", "los", "las", "y", "van", "von", "der", "den", "op",
}
# 成立年份后缀：18xx/19xx/20xx 四位数字（Parma Calcio 1913 → parma）
_YEAR_RE = re.compile(r"^(1[89]\d{2}|20[0-2]\d)$")


def _strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm_team(name):
    """归一化队名：小写、去重音、去句点与间隔符、剥离俱乐部前后缀与成立年份，用于兜底匹配。"""
    if not name:
        return ""
    s = _strip_accents(str(name)).lower()
    s = s.replace("·", " ").replace(".", " ").replace("-", " ").replace("'", " ")
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff ]", " ", s)
    toks = s.split()
    out = []
    for t in toks:
        if len(toks) > 1 and (t in _CLUB_TOKENS or _YEAR_RE.match(t) or t in _FILLER_TOKENS):
            continue
        out.append(t)
    s = " ".join(out).strip()
    # 二次清理可能残留的单字符/标点
    s = re.sub(r"\s+", " ", s).strip()
    return s


# 中文字形/异体归一化表：竞彩与数据源常出现同音/形近异写（如 乌得勒支/乌德勒支）。
# 仅收录「不会造成误匹配」的强等价对（同一俱乐部两种通行写法）。
_CHAR_VARIANT = {
    "得": "德",   # 乌得勒支（数据源）↔ 乌德勒支（竞彩）
    "圖": "图",   # 繁简
    "兒": "尔",   # 繁简
    "裏": "里",
    "雲": "云",
    "岡": "岗",
    "濱": "滨",
    "島": "岛",
    "諾": "诺",
    "爾": "尔",
}


# 中文同队异写归一（整串精确替换，先长后短）：
# 竞彩同一球队存在多种通行译名（曼彻斯特联/曼联、莱比锡红牛/RB莱比锡），
# whoscored 侧经映射表翻译出的中文名若与竞彩写法不一致，双队名精确比对会失配。
# 在 zh_norm 最前面做整串归一，两侧都归一到主译名后再比对。
_ZH_ALIAS = {
    "曼彻斯特联": "曼联",
    "曼彻斯特城队": "曼彻斯特城",
    "莱比锡红牛": "RB莱比锡",
    "西悉尼漫步者": "西悉尼流浪者",
    "曼谷港务": "泰港",
    "柔佛": "柔佛DT",
    "布里兰": "武里南联",
    "巴黎圣日尔曼": "巴黎圣日耳曼",
    "浦和红钻队": "浦和红钻",
    "那不勒斯队": "那不勒斯",
    "国际米兰队": "国际米兰",
    "尤文图斯队": "尤文图斯",
    "基多体育大学": "基多体育大学",
    # 竞彩官方译名兜底（数据源/民间常写异体字，v1.7.26 补：
    # Portsmouth 竞彩官方名「朴次茅斯」，英系数据源常写「朴茨茅斯」，
    # 曾因一字之差导致周二003 狼队vs朴次茅斯 的 WhoScored 预测挂不上竞彩编号）
    "朴茨茅斯": "朴次茅斯",
    # 博多闪耀两种译名归一（同一俱乐部，竞彩官方名「博德闪耀」）
    "博多格林特": "博德闪耀",
}


def zh_norm(name):
    """中文字形/异写归一：已知异体、繁简字与同队异写统一，便于跨源队名精确比对。"""
    if not name:
        return ""
    s = str(name)
    # 整串异写归一（先试全串；键已按长→短排布意识书写，无前缀连锁风险）
    if s in _ZH_ALIAS:
        s = _ZH_ALIAS[s]
    s = "".join(_CHAR_VARIANT.get(ch, ch) for ch in s)
    return re.sub(r"\s+", "", s).strip()


# 构建 TEAM_EN2ZH：英文（原始/归一化）→ 竞彩中文名
TEAM_EN2ZH = {}
TEAM_EN2ZH_ALL = {}  # 英文（原始/归一化）→ 全部中文变体列表（v1.7.26，匹配兜底用）
_ZH2EN = {}  # 竞彩中文名 -> 英文（用于反向校验/调试）
for _line in _TEAM_SRC.strip().splitlines():
    _line = _line.strip()
    if not _line or "|" not in _line:
        continue
    _parts = [p.strip() for p in _line.split("|") if p.strip()]
    if len(_parts) < 2:
        continue
    _zh = _parts[0]
    _ZH2EN[_zh] = _parts[1]
    for _en in _parts[1:]:
        _en = _en.strip()
        if not _en:
            continue
        TEAM_EN2ZH[_en] = _zh
        # 归一化键：小写 norm 与 Title 版都注册，兼容 WhoScored 全称变体
        # （如 "Parma Calcio 1913" → norm "parma" 命中表内 "Parma" 条目）
        _n = norm_team(_en)
        if _n:
            TEAM_EN2ZH.setdefault(_n, _zh)
            TEAM_EN2ZH.setdefault(_n.title(), _zh)
        # 同一英文别名的全部中文变体（v1.7.26）：表内不同分区可能给同一英文队名
        # 写了不同中文译名（如 Portsmouth→朴次茅斯/朴茨茅斯），TEAM_EN2ZH 后者
        # 覆盖前者。竞彩编号匹配（_match_jc_num）若只认单一译名，遇到覆盖后的
        # 译名与竞彩官方名不一致就会失配。这里保留全集供匹配侧兜底。
        for _k in (_en, _n, _n.title() if _n else ""):
            if not _k:
                continue
            _lst = TEAM_EN2ZH_ALL.setdefault(_k, [])
            if _zh not in _lst:
                _lst.append(_zh)


def en_to_zh(en_name):
    """WhoScored 英文队名 → 竞彩中文名；未收录返回原串。"""
    if not en_name:
        return en_name
    if en_name in TEAM_EN2ZH:
        return TEAM_EN2ZH[en_name]
    _n = norm_team(en_name)
    if not _n:
        return en_name
    return TEAM_EN2ZH.get(_n) or TEAM_EN2ZH.get(_n.title()) or en_name


def en_to_zh_all(en_name):
    """WhoScored 英文队名 → 表内登记的全部中文变体（含未覆盖主键的归一化键）。

    v1.7.26 新增：用于竞彩编号匹配兜底——主译名（TEAM_EN2ZH，后行覆盖前行）
    与竞彩官方名不一致时（朴茨茅斯 vs 朴次茅斯），任一中文变体与竞彩队名
    （zh_norm 后）相等即算命中，杜绝「一字之差整场丢预测」。
    """
    if not en_name:
        return []
    out = list(TEAM_EN2ZH_ALL.get(en_name) or [])
    _n = norm_team(en_name)
    if _n:
        for k in (_n, _n.title()):
            for z in (TEAM_EN2ZH_ALL.get(k) or []):
                if z not in out:
                    out.append(z)
    return out


def zh_to_en(zh_name):
    """竞彩中文名 → 英文（用于调试/反向）。"""
    return _ZH2EN.get(zh_name, zh_name)


if __name__ == "__main__":
    # 自检：报告当前 WhoScored 队名能否翻译
    import json, os
    HERE = os.path.dirname(os.path.abspath(__file__))
    wp = os.path.join(HERE, "whoscored_data.json")
    if os.path.exists(wp):
        d = json.load(open(wp, encoding="utf-8"))
        miss = []
        for m in d.get("matches", []):
            for side in ("home", "away"):
                t = m.get(side)
                if t and en_to_zh(t) == t:
                    if t not in miss:
                        miss.append(t)
        print(f"Whoscored 队名总数中未翻译: {len(miss)}")
        for t in sorted(miss):
            print("  未翻译:", t)
    print(f"TEAM_EN2ZH 条目数: {len(TEAM_EN2ZH)}")
