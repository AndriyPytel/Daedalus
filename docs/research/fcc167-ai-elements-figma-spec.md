# FCC-167 — Figma spec, AI Elements ▲ Vercel (Community) (Copy)
File: https://www.figma.com/design/1PvKcQ3bmAbgMPHdkncOfl/AI-Elements---%E2%96%B2-Vercel--Community---Copy-
Snapshotted via `~/bin/figma` (FIGMA_TOKEN_2), 63,252 nodes, 51 pages.

## Queue
```
COMPONENT Queue with PromptInput  (4063:2543)  page: Queue
  box     600 x 312  @ 80,687
  layout  VERTICAL  gap None  padding 0,0,0,0
  children:
    FRAME     padding                          600x150
    INSTANCE  PromptInput                      600x162  #ffffff
```
```
COMPONENT QueueList  (4035:19172)  page: Queue
  box     441 x 160  @ 1435,408
  layout  VERTICAL  gap None  padding 0,16.0,-4.0,0
  children:
    INSTANCE  QueueItem                        425x52
    INSTANCE  QueueItem                        425x52
    INSTANCE  QueueItem                        425x52
    INSTANCE  QueueItem                        425x52
    INSTANCE  QueueItem                        425x52
    INSTANCE  QueueItem                        425x52
```
```
COMPONENT QueueItemFile  (4035:20037)  page: Queue
  box     134 x 26  @ 1435,608
  layout  HORIZONTAL  gap 4.0  padding 4.0,8.0,4.0,8.0
  fill    #f5f5f5
  radius  6.0
  children:
    INSTANCE  svg                              12x12
    TEXT      Lorem ipsum dolor sit amet       100x16  #0a0a0a  Geist 12/500  “Lorem ipsum dolor sit amet”
    INSTANCE  svg                              12x12
```
```
COMPONENT QueueItemImage  (4064:3000)  page: Queue
  box     32 x 32  @ 1624,608
  layout  VERTICAL  gap None  padding 0,0,0,0
  radius  6.0
  children:
    FRAME     img                              32x32
```

## Task (Plan Steps candidate)
```
COMPONENT ⚛️ children=TaskItem x5  (2005:15411)  page: Task
  box     240 x 142  @ 927,329
  layout  VERTICAL  gap 8.0  padding 0,8.0,0,16.0
  children:
    INSTANCE  TaskItem                         214x22
    INSTANCE  TaskItem                         214x22
    INSTANCE  TaskItem                         214x22
    INSTANCE  TaskItem                         214x22
    INSTANCE  TaskItem                         214x22
```
```
COMPONENT ⚛️ children=TaskItem x3  (3126:24252)  page: Task
  box     240 x 82  @ 927,519
  layout  VERTICAL  gap 8.0  padding 0,8.0,0,16.0
  children:
    INSTANCE  TaskItem                         214x22
    INSTANCE  TaskItem                         214x22
    INSTANCE  TaskItem                         214x22
```

## Reasoning
```
COMPONENT_SET Reasoning  (2045:17582)  page: Reasoning
  box     896 x 612  @ 283,105
  radius  5.0
  children:
    COMPONENT isStreaming=true, hover=false,   400x112
    COMPONENT isStreaming=true, hover=true,    400x112
    COMPONENT isStreaming=true, hover=false,   400x36
    COMPONENT isStreaming=true, hover=true,    400x36
    COMPONENT isStreaming=false, hover=false   400x212
    COMPONENT isStreaming=false, hover=true,   400x212
    COMPONENT isStreaming=false, hover=false   400x36
    COMPONENT isStreaming=false, hover=true,   400x36
```
```
COMPONENT_SET ReasoningTrigger  (2008:16152)  page: Reasoning
  box     417 x 272  @ 1446,105
  radius  5.0
  children:
    COMPONENT state=default, isStreaming=tru   103x20
    COMPONENT state=default, isStreaming=fal   218x20
    COMPONENT state=default, isStreaming=tru   103x20
    COMPONENT state=default, isStreaming=fal   218x20
    COMPONENT state=hover, isStreaming=true,   103x20
    COMPONENT state=hover, isStreaming=false   218x20
    COMPONENT state=hover, isStreaming=true,   103x20
    COMPONENT state=hover, isStreaming=false   218x20
```

## Chain of Thought (reasoning trail candidate)
```
COMPONENT_SET ChainOfThought  (2004:14349)  page: Chain of Thought
  box     448 x 652  @ 153,80
  radius  5.0
  children:
    COMPONENT open=false                       400x20
    COMPONENT open=true                        400x536
```
```
COMPONENT_SET ChainOfThoughtHeader  (2410:13186)  page: Chain of Thought
  box     400 x 160  @ 681,80
  layout  VERTICAL  gap 16.0  padding 16.0,16.0,16.0,16.0
  radius  5.0
  children:
    COMPONENT state=default, open=false        368x20
    COMPONENT state=hover, open=false          368x20
    COMPONENT state=default, open=true         368x20
    COMPONENT state=hover, open=true           368x20
```
```
COMPONENT_SET ChainOfThoughtStep  (2008:17297)  page: Chain of Thought
  box     400 x 460  @ 681,304
  layout  VERTICAL  gap 16.0  padding 16.0,16.0,16.0,16.0
  radius  5.0
  children:
    COMPONENT status=complete                  368x132
    COMPONENT status=pending                   368x132
    COMPONENT status=active                    368x132
```

## Prompt Input
```
COMPONENT_SET PromptInput  (4768:192396)  page: Prompt Input
  box     648 x 896  @ 205,36
  radius  5.0
  children:
    COMPONENT state=placeholder                600x162  #ffffff
    COMPONENT state=filled                     600x162  #ffffff
    COMPONENT state=filled + attachments       600x198  #ffffff
    COMPONENT state=focused                    600x162  #ffffff
```
```
COMPONENT_SET PromptInputCommand  (4099:28829)  page: Prompt Input
  box     659 x 316  @ 1916,460
  layout  HORIZONTAL  gap 27.0  padding 16.0,16.0,16.0,16.0
  radius  5.0
  children:
    COMPONENT empty=false                      300x284
    COMPONENT empty=true                       300x80
```

## Model Selector
```
COMPONENT_SET ModelSelectorTrigger (New)  (4557:33493)  page: Model Selector
  box     345 x 64  @ -1594,1199
  radius  5.0
  children:
    COMPONENT display=inline                   86x32
    COMPONENT display=block                    200x32
```
```
COMPONENT_SET ModelSelectorContent  (4369:184067)  page: Model Selector
  box     1072 x 382  @ -2103,737
  radius  5.0
  children:
    COMPONENT state=empty                      512x118  #ffffff
    COMPONENT state=default                    512x350  #ffffff
```
```
COMPONENT ModelSelectorList  (4451:15562)  page: Model Selector
  box     510 x 1652  @ -775,145
  layout  VERTICAL  gap None  padding 0,0,0,0
  children:
    INSTANCE  Parts / CommandGroup             510x164
    INSTANCE  Parts / CommandGroup             510x164
    INSTANCE  Parts / CommandGroup             510x132
    INSTANCE  Parts / CommandGroup             510x164
    INSTANCE  Parts / CommandGroup             510x132
    INSTANCE  Parts / CommandGroup             510x132
    INSTANCE  Parts / CommandGroup             510x132
    INSTANCE  Parts / CommandGroup             510x100
    INSTANCE  Parts / CommandGroup             510x132
    INSTANCE  Parts / CommandGroup             510x100
    INSTANCE  Parts / CommandGroup             510x100
    INSTANCE  Parts / CommandGroup             510x68
    INSTANCE  Parts / CommandGroup             510x132
```
```
COMPONENT ModelSelectorDialog  (4451:14875)  page: Model Selector
  box     1024 x 512  @ -2103,145
  layout  VERTICAL  gap 8.0  padding 48.0,16.0,48.0,16.0
  children:
    INSTANCE  ModelSelectorContent             512x348  #ffffff
```

## Plan
```
COMPONENT _plan-content-example  (4930:33068)  page: Plan
  box     484 x 264  @ 185,1095
  layout  VERTICAL  gap 16.0  padding 0,0,0,0
  children:
    FRAME     content                          484x88
    FRAME     content                          484x160
```

## Confirmation
```
COMPONENT ConfirmationRequest / generic  (4346:16869)  page: Confirmation
  box     380 x 20  @ 1645,245
  layout  VERTICAL  gap 2.0  padding 0,0,0,0
  children:
    TEXT      text                             380x20  #737373  Geist 14/400  “This tool wants to access your file system. Do you”
```
```
COMPONENT ConfirmationAccepted  (4348:1198)  page: Confirmation
  box     283 x 20  @ 1645,125
  layout  HORIZONTAL  gap 4.0  padding 0,0,0,0
  children:
    TEXT      AlertTitle                       68x20  #0a0a0a  Geist 14/500  “Accepted:”
    TEXT      AlertDescription                 211x20  #737373  Geist 14/400  “You approved this tool execution.”
```
```
COMPONENT ConfirmationRejected  (4348:1199)  page: Confirmation
  box     270 x 20  @ 1645,185
  layout  HORIZONTAL  gap 4.0  padding 0,0,0,0
  children:
    TEXT      AlertTitle                       63x20  #0a0a0a  Geist 14/500  “Rejected:”
    TEXT      AlertDescription                 203x20  #737373  Geist 14/400  “You rejected this tool execution.”
```
```
COMPONENT _ConfirmationActions  (4346:15375)  page: Confirmation
  box     137 x 28  @ 2273,205
  layout  HORIZONTAL  gap 8.0  padding 0,0,0,0
  children:
    INSTANCE  Button                           59x28  #ffffff
    INSTANCE  Button                           70x28  #155dfc
```

## Test Results
```
COMPONENT TestResults  (4593:4186)  page: Test Results
  box     418 x 490  @ 0,0
  layout  VERTICAL  gap None  padding 0,0,0,0
  fill    #ffffff
  radius  10.0
  children:
    INSTANCE  TestResultsHeader                416x47
    INSTANCE  _TestResultsProgress             416x57
    SLOT      TestResultsContent               416x384
```
```
COMPONENT TestResultsHeader  (4593:3534)  page: Test Results
  box     416 x 47  @ 2861,0
  layout  HORIZONTAL  gap 8.0  padding 12.0,16.0,12.0,16.0
  children:
    FRAME     TestResultsSummary               257x22
    TEXT      TestResultsDuration              37x20  #737373  Geist 14/400  “3.25s”
```
```
COMPONENT_SET Test  (4591:133543)  page: Test Results
  box     353 x 224  @ 2292,0
  layout  VERTICAL  gap 16.0  padding 16.0,16.0,16.0,16.0
  radius  5.0
  children:
    COMPONENT status=passed                    321x36
    COMPONENT status=skipped                   321x36
    COMPONENT status=failed                    321x36
    COMPONENT status=running                   321x36
```
```
COMPONENT_SET TestSuiteHeader  (4591:133175)  page: Test Results
  box     400 x 256  @ 659,0
  layout  VERTICAL  gap 16.0  padding 16.0,16.0,16.0,16.0
  radius  5.0
  children:
    COMPONENT state=default, open=false        368x44
    COMPONENT state=hover, open=false          368x44
    COMPONENT state=default, open=true         368x44
    COMPONENT state=hover, open=true           368x44
```
```
COMPONENT TestError  (4591:133550)  page: Test Results
  box     316 x 116  @ 2292,264
  layout  VERTICAL  gap None  padding 8.0,8.0,8.0,8.0
  children:
    FRAME     Frame 53                         300x100
```
```
COMPONENT _TestResultsProgress  (4591:133898)  page: Test Results
  box     191 x 32  @ 2861,87
  layout  VERTICAL  gap 8.0  padding 0,0,0,0
  children:
    FRAME     Progress                         191x8
    FRAME                                      191x16
```

## Context (usage/cost)
```
COMPONENT ContextContent  (2162:13731)  page: Context
  box     256 x 170  @ 1024,105
  layout  VERTICAL  gap None  padding 0,0,0,0
  fill    #ffffff
  radius  10.0
  children:
    FRAME     ContextContentHeader             256x48
    FRAME     ContextContentBody               256x85
    FRAME     ContextContentFooter             256x37  #f5f5f5
```
```
COMPONENT _ContextIcon  (2009:20593)  page: Context
  box     20 x 20  @ 1520,110
  children:
    ELLIPSE   circle                           17x17
    ELLIPSE   arc                              17x17
```

## Assets (icons)
```
SECTION Icons - Lucide  (2165:5292)  page: Assets
  box     1273 x 1377  @ -97,-4
  fill    #ffffff
  children:
    FRAME                                      408x984
    TEXT      Icons - c/o Lucide Icons         155x20  #155dfc  Geist 14/500  “Icons - c/o Lucide Icons ”
    TEXT      Filled Icons                     73x20  #737373  Geist 14/400  “Filled Icons”
    COMPONENT <CircleIcon> | Filled            24x24
    COMPONENT <ThumbsDownIcon> | Filled        24x24
    COMPONENT <ThumbsUpIcon> | Filled          24x24
    COMPONENT <HeartIcon> | Filled             24x24
    FRAME                                      568x416  #f5f5f5
    COMPONENT <CircleSmall> | Filled           24x24
```
```
SECTION Icons - Logos  (2045:13108)  page: Assets
  box     568 x 1100  @ 1216,273
  fill    #ffffff
  children:
    TEXT      Logos                            39x20  #737373  Geist 14/400  “Logos”
    TEXT      Vercel, the Vercel design, Nex   408x48  #737373  Geist 12/400  “Vercel, the Vercel design, Next.js and related mar”
    FRAME                                      408x792
```
```
SECTION Icons - Files  (3612:22384)  page: Assets
  box     568 x 244  @ 1216,0
  fill    #ffffff
  children:
    TEXT      File Icons - c/o Simple Icons    183x20  #155dfc  Geist 14/500  “File Icons - c/o Simple Icons”
    COMPONENT <SiReact>                        24x24
    COMPONENT <SiTypescript>                   24x24
    COMPONENT <SiJavascript>                   24x24
    COMPONENT <SiCss>                          24x24
    COMPONENT <SiJson>                         24x24
    COMPONENT <SiHtml5>                        24x24
    COMPONENT <SiMarkdown>                     24x24
```

