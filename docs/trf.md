SCHOOL OF COMPUTING AND CREATIVE MEDIA

|     | UTS |     | BACHELOR OF COMPUTER SCIENCE (HONS.)  |     |     |
| --- | --- | --- | ------------------------------------- | --- | --- |

|     |     |     | Title Registration Form (Final Year Project)  |     |     |
| --- | --- | --- | --------------------------------------------- | --- | --- |

Details of Student
Student Name:  VINCENT LEE CHENG HONG  Project Category   System/Application Development
■
|              |     |              | (tick one):  |     |                 |
| ------------ | --- | ------------ | ------------ | --- | --------------- |
| Student ID:  |     | BCS24020018  |              |     | Research-Based  |
☐
| Phone Num:  |     | 011-1400 4966  |     |     | Hybrid (Research + Development)  |
| ----------- | --- | -------------- | --- | --- | -------------------------------- |
☐
Programme:  Bachelor of Computer Science (Hons)  Proposed  AI Local News Summarization and Alert Bot on
Semester / Academic Year:  FEB 2025/2026  Project Title:  Telegram

| Details of Supervisor  |                                 |     | Details of Co-Supervisor (optional)  |     |     |
| ---------------------- | ------------------------------- | --- | ------------------------------------ | --- | --- |
| Name:                  | Ms. Khairunnisa Ibrahim         |     | Name:                                |     |     |
| Email:                 | khairunnisa.ibrahim@uts.edu.my  |     | Email:                               |     |     |
| Phone:                 |                                 |     | Phone:                               |     |     |
011-25182733

|     | Signature of Student:  |     |     | Acknowledged by:  |     |
| --- | ---------------------- | --- | --- | ----------------- | --- |
|     |                        |     |     |                   |     |
|     |                        |     |     |                   |     |
|     |                        |     |     |                   |     |
Name: VINCENT LEE CHENG HONG  Name of Supervisor: Ms. KHAIRUNNISA IBRAHIM
| Date: 24/02/2026  |     |     | Date:  27/2/2026 |     |     |
| ----------------- | --- | --- | ---------------- | --- | --- |

Project Title & Short Description  Current Problem/Pain Point/Challenges  Value Brought by the Project
| Intelligent  | Local  | News  | Summarization  |     | and  |     |     |     |     |     |
| ------------ | ------ | ----- | -------------- | --- | ---- | --- | --- | --- | --- | --- |
•  Fragmented  Sources:  Local  news  is  •  Enhanced Community Awareness (SDG
often  trapped  in  closed  social  media  11):  Increases  local  engagement  by
Alerting Bot on Telegram Using Local LLM.
platform  like  Facebook,  making  it  ensuring residents are informed about
|     | •  A  backend-driven  |        | automated  |            | system  |               |            |          |         |                                            |
| --- | --------------------- | ------ | ---------- | ---------- | ------- | ------------- | ---------- | -------- | ------- | ------------------------------------------ |
|     |                       |        |            |            |         | inaccessible  | to  users  | without  | active  | safety, infrastructure, events, and news.  |
|     | that  scrapes         | local  | news,      | processes  | it      |               |            |          |         |                                            |
accounts.
•  Time Efficiency: Saves users time by
using a locally hosted AI to generate
•  Information Overload: Mainstream apps  providing  AI-generated  summaries  in
short summaries, and pushes scheduled
|     |     |     |     |     |     | prioritize  | global  headlines,  |     | lack  of  | Telegram chat.  |
| --- | --- | --- | --- | --- | --- | ----------- | ------------------- | --- | --------- | --------------- |
updates directly to users via a Telegram
critical community updates like utility  •  Privacy & Cost-Effectiveness: Utilizes a
Bot.
|     |     |     |     |     |     | cuts or local news.  |     |     |     | Local LLM to process data, eliminating  |
| --- | --- | --- | --- | --- | --- | -------------------- | --- | --- | --- | --------------------------------------- |
•  It aims to bridge the information gap for
•  Inefficient Consumption: Local reports  subscription  costs  for  third-party  AI
|     | younger        | generations  |             | and          | busy  |             |                      |                     |       |        |
| --- | -------------- | ------------ | ----------- | ------------ | ----- | ----------- | -------------------- | ------------------- | ----- | ------ |
|     |                |              |             |              |       | are  often  | lengthy              | and  unstructured,  |       | APIs.  |
|     | professionals  | by           | delivering  | relevant     |       |             |                      |                     |       |        |
|     |                |              |             |              |       | preventing  | busy  professionals  |                     | from  |        |
|     | community      | news         | without     | information  |       |             |                      |                     |       |        |
quickly extracting key information.
overload.
Scope (Target user, tools, etc.)  Similar System/Application/Research  Proposed Project Features
•  Target  Users:  Young  adults,  busy  •  Google  News:  Excellent  for  •  Automated  Aggregation:  Scrapes  and
working  professionals,  and  local  global/national news but lacks "local"  compiles  news  from  defined  local
community residents in Sarawak.  sources like specific area updates.  sources  such  as  RSS  feeds,  local
•  Platform:  Telegram Messaging App.  •  Facebook  Community:  Rich  in  local  websites.
•  Tools/Technologies:  info but unstructured, filled with noise  •  AI  Summarization:  Automatically
o  Tools: Telegram BotFather API.  or spam, and requires an active account  converts  long  articles  into  30-word
o  Backend: Python.  to browse.  briefs  or  bullet  points  using  a  Local
o  AI:  Ollama  (Llama  3.1)  for  •  Official WhatsApp Channels: Delivers  LLM.
summarization.  updates directly, but requires users to  •  User  Personalization: Allows  users  to
o  Data: RSS Feeds, Web Scrapers.  subscribe  to  multiple  publishers  configure their bot settings to filter news
•  Limitations:  The bot requires the host  individually.  by  specific  categories,  locations,  and
machine  (my  laptop)  to  be  online  to  •  Standard  Telegram  RSS  Bots:  User  preferred push frequency.
process and push messages; it does not  pastes  an  RSS  link,  and  the  bot  •  Urgency  Notification:  Analyzes  the
feature a custom graphical user interface  automatically  forwards  every  new  news to send push notifications only for
(GUI).  article to the chat  high-priority  local  incidents  such  as
floods.