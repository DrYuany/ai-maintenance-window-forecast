# 评测结果（backend）

> 由 `python scripts/bench_suite.py --suite backend --save` 生成，基准日期 2026-10-30。
> 模型侧同一句重跑可能不同，这个表是**一次快照**。

**221 条：通过 199，未通过 22（90%）。需重试 19 条，平均 2068 ms。**

归因：通过 199；判断分歧（route 不同，其余都对） 17；判据误伤（route 对、验收退） 3；其他 2

| # | 场景 | 原话 | route | 期望 | 结果 |
|---|---|---|---|---|---|
| Q001 | backend·chitchat | 你好 | not_change | not_change | 过 |
| Q002 | backend·chitchat | 在吗 | not_change | not_change | 过 |
| Q003 | backend·chitchat | 你叫什么名字 | not_change | not_change | 过 |
| Q004 | backend·chitchat | 谢谢你的帮助 | not_change | not_change | 过 |
| Q005 | backend·chitchat | 今天天气怎么样 | not_change | not_change | 过 |
| Q006 | backend·chitchat | 帮我写首诗吧 | not_change | not_change | 过 |
| Q007 | backend·chitchat | 这个产品卖多少钱 | not_change | not_change | 过 |
| Q008 | backend·chitchat | 再见 | not_change | not_change | 过 |
| Q010 | backend·query_data | 现在风速多少 | not_change | not_change | 过 |
| Q011 | backend·query_data | 未来三天电价怎么样 | not_change | not_change | 过 |
| Q012 | backend·query_data | 叶片库存还剩几套 | not_change | not_change | 过 |
| Q013 | backend·query_data | 现在有哪些告警 | not_change | not_change | 过 |
| Q014 | backend·query_data | 昨晚新增了什么告警 | not_change | not_change | 过 |
| Q015 | backend·query_data | 风机018是什么型号 | not_change | not_change | 过 |
| Q016 | backend·query_data | 齿轮箱维修要多久 | not_change | not_change | 过 |
| Q017 | backend·query_data | 船租现在多少钱一天 | not_change | not_change | 过 |
| Q018 | backend·query_data | 窗口02什么时候开始 | not_change | not_change | 过 |
| Q019 | backend·query_data | 最近七天海况如何 | not_change | not_change | 过 |
| Q020 | backend·query_data | 备件够不够用 | not_change | not_change | 过 |
| Q021 | backend·query_data | 高严重度任务的截止窗口是哪个 | not_change | not_change | 过 |
| Q030 | backend·query_plan | 这版方案总成本多少 | not_change | not_change | 过 |
| Q031 | backend·query_plan | 完成任务数是多少 | not_change | not_change | 过 |
| Q032 | backend·query_plan | 建议窗口是哪个 | not_change | not_change | 过 |
| Q033 | backend·query_plan | 船天用了多少 | not_change | not_change | 过 |
| Q034 | backend·query_plan | 最晚完工是什么时候 | not_change | not_change | 过 |
| Q035 | backend·query_plan | 方案01的成本构成是什么 | not_change | not_change | 过 |
| Q036 | backend·query_plan | 现在解出来是可行还是最优 | not_change | not_change | 过 |
| Q037 | backend·query_plan | 还剩多少任务没排 | not_change | not_change | 过 |
| Q038 | backend·query_plan | 这版用了什么策略 | not_change | not_change | 过 |
| Q039 | backend·query_plan | 数据是什么时候的快照 | not_change | not_change | 过 |
| Q050 | backend·explain_model | 模型长什么样 | not_change | not_change | 过 |
| Q051 | backend·explain_model | 目标函数怎么定义的 | not_change | not_change | 过 |
| Q052 | backend·explain_model | 发电损失怎么算的 | not_change | not_change | 过 |
| Q053 | backend·explain_model | 为什么用0-1变量不用连续变量 | not_change | not_change | 过 |
| Q054 | backend·explain_model | 船租为什么按自然日计 | not_change | not_change | 过 |
| Q055 | backend·explain_model | 约束有哪些种类 | not_change | not_change | 过 |
| Q056 | backend·explain_model | 为什么海况差就不能开工 | not_change | not_change | 过 |
| Q057 | backend·explain_model | 这个需求是怎么建模的 | not_change | not_change | 过 |
| Q060 | backend·explain_plan | 为什么选窗口02 | not_change | not_change | 过 |
| Q061 | backend·explain_plan | 任务018为什么没排上 | not_change | not_change | 过 |
| Q062 | backend·explain_plan | 为什么这版成本比上版高 | not_change | not_change | 过 |
| Q063 | backend·explain_plan | 这个排程是怎么排出来的 | not_change | not_change | 过 |
| Q064 | backend·explain_plan | 为什么先修风机001 | not_change | not_change | 过 |
| Q065 | backend·explain_plan | 为什么只修了18个 | not_change | not_change | 过 |
| Q066 | backend·explain_plan | 这版方案风险在哪 | not_change | not_change | 过 |
| Q067 | backend·explain_plan | 给我讲讲这个排程的思路 | not_change | not_change | 过 |
| Q070 | backend·compare | 对比方案01和02 | not_change | not_change | 过 |
| Q071 | backend·compare | 这版和上一版差在哪 | not_change | not_change | 过 |
| Q072 | backend·compare | 哪个方案更省钱 | not_change | not_change | 过 |
| Q073 | backend·compare | 经济优先和风险优先差多少 | not_change | not_change | 过 |
| Q074 | backend·compare | 方案01、02、03一起对比，基准是01 | clarify | not_change | route=clarify（要 ['not_change']） |
| Q075 | backend·compare | 这两版完成任务一样吗 | not_change | not_change | 过 |
| Q076 | backend·compare | 换策略后结果差多少 | not_change | not_change | 过 |
| Q077 | backend·compare | 帮我比较下窗口02和窗口03的方案 | not_change | not_change | 过 |
| Q100 | backend·modify_parameter | 船租改成50万 | dsl | dsl | 过 |
| Q101 | backend·modify_parameter | 船租上涨20% | dsl | dsl | 过 |
| Q102 | backend·modify_parameter | 船租翻一倍 | dsl | dsl | 过 |
| Q103 | backend·modify_parameter | 人工单价改成300元 | dsl | dsl | 过 |
| Q104 | backend·modify_parameter | 最多两条作业线 | dsl | dsl | 过 |
| Q105 | backend·modify_parameter | 每天最多8小时 | dsl | dsl | 过 |
| Q106 | backend·modify_parameter | 人员上限改成20人 | dsl | dsl | 过 |
| Q107 | backend·modify_parameter | 风机间转移时间改成1小时 | dsl | dsl | 过 |
| Q108 | backend·modify_parameter | 每天8点开工 | dsl | dsl | 过 |
| Q109 | backend·modify_parameter | 每天14点收工 | dsl | dsl | 过 |
| Q110 | backend·modify_parameter | 只上两条船 | dsl | dsl | 过 |
| Q111 | backend·modify_parameter | 船少一条看看 | dsl | dsl | 过 |
| Q112 | backend·modify_parameter | 每天只能干8小时 | dsl | dsl | 过 |
| Q113 | backend·modify_parameter | 少干点，每天6小时 | dsl | dsl | 过 |
| Q114 | backend·modify_parameter | 人手砍一半 | dsl | dsl | 过 |
| Q115 | backend·modify_parameter | 风机之间挪动要3小时 | dsl | dsl | 过 |
| Q116 | backend·modify_parameter | 早上8点开始干活 | dsl | dsl | 过 |
| Q117 | backend·modify_parameter | 人员上限设为2人 | dsl | dsl | 过 |
| Q120 | backend·modify_data | 叶片备件11月5日到货 | dsl | dsl | 过 |
| Q121 | backend·modify_data | 叶片备件11月5日到货，之前不能开工 | dsl | dsl | 过 |
| Q122 | backend·modify_data | 齿轮箱维修工时上涨20% | dsl | dsl | 过 |
| Q123 | backend·modify_data | 齿轮箱修起来更费时了，加20% | dsl | dsl | 过 |
| Q124 | backend·modify_data | 严重故障11月8日前必须修完 | clarify | dsl | route=clarify（要 ['dsl']） |
| Q125 | backend·modify_data | 高危的8号前修完 | clarify | dsl | route=clarify（要 ['dsl']） |
| Q126 | backend·modify_data | 中等故障9号前搞定 | clarify | clarify/dsl/not_change/reject | 验收退回 A4/A7 |
| Q127 | backend·modify_data | 补库延期2天 | clarify | dsl | route=clarify（要 ['dsl']） |
| Q140 | backend·modify_constraint | 任务001和任务002不能在同一天出海维修 | dsl | dsl | 过 |
| Q141 | backend·modify_constraint | 001和002别放同一天 | dsl | dsl | 过 |
| Q142 | backend·modify_constraint | 这两台风机别同时出海 | clarify | clarify | 过 |
| Q143 | backend·modify_constraint | T01和T02不能同时修 | dsl | dsl | 过 |
| Q144 | backend·modify_constraint | 先修任务001再修任务022 | dsl | dsl | 过 |
| Q145 | backend·modify_constraint | 001修完才能动002 | dsl | dsl | 过 |
| Q146 | backend·modify_constraint | 任务018只能上2号船 | dsl | dsl | 过 |
| Q147 | backend·modify_constraint | 018用2号线 | dsl | dsl | 过 |
| Q148 | backend·modify_constraint | 任务001最早11月5日开工 | dsl | dsl | 过 |
| Q149 | backend·modify_constraint | 001最早5号才能开工 | dsl | dsl | 过 |
| Q150 | backend·modify_constraint | 任务001最晚11月6日前修完 | dsl | dsl | 过 |
| Q151 | backend·modify_constraint | 001六号前修完 | dsl | dsl | 过 |
| Q152 | backend·modify_constraint | 11月3号不要开工 | dsl | dsl | 过 |
| Q153 | backend·modify_constraint | 3号那天不要动工 | clarify | dsl | route=clarify（要 ['dsl']） |
| Q154 | backend·modify_constraint | 完工时间和不超过180000分钟 | dsl | dsl | 过 |
| Q155 | backend·modify_constraint | 至少完成8项任务 | dsl | dsl | 过 |
| Q156 | backend·modify_constraint | 任务005必须修 | dsl | dsl | 过 |
| Q157 | backend·modify_constraint | 别排任务003 | dsl | dsl | 过 |
| Q158 | backend·modify_constraint | 总完工时间压一点 | clarify | clarify/dsl/not_change/reject | 过 |
| Q159 | backend·modify_constraint | 完成数不要低于8个 | dsl | dsl | 过 |
| Q160 | backend·modify_constraint | 同一条船干完一个活要歇2小时 | reject | clarify | route=reject（要 ['clarify']） |
| Q161 | backend·modify_constraint | 2号船周三那天别排它 | clarify | clarify | 过 |
| Q170 | backend·modify_objective | 按高风险优先抢修 | dsl | dsl | 过 |
| Q171 | backend·modify_objective | 先修严重的 | dsl | dsl | 过 |
| Q172 | backend·modify_objective | 改成成本最低优先 | dsl | dsl | 过 |
| Q173 | backend·modify_objective | 省钱优先 | dsl | dsl | 过 |
| Q174 | backend·modify_objective | 改成加权，高严重完成数0.6总经济成本0.4 | clarify | dsl | route=clarify（要 ['dsl']） |
| Q175 | backend·modify_objective | 权重改一下，严重故障占六成 | clarify | dsl | route=clarify（要 ['dsl']） |
| Q176 | backend·modify_objective | 先最小化完工时间和，再最小化成本 | dsl | dsl | 过 |
| Q177 | backend·modify_objective | 尽量让大家都早点干完 | dsl | dsl | 过 |
| Q178 | backend·modify_objective | 最短完成优先 | dsl | dsl | 过 |
| Q179 | backend·modify_objective | 检修最多器件优先 | dsl | dsl | 过 |
| Q190 | backend·modify_batch | 先按默认方法算一遍，再按每天8小时算一遍，然后对比 | clarify | clarify/not_change/reject | 验收退回 A8 |
| Q191 | backend·modify_batch | 算两个场景：船租涨20%和最多两条线，对比一下 | clarify | clarify/not_change/reject | 过 |
| Q192 | backend·modify_batch | 先算成本优先再算风险优先，看看差多少 | clarify | clarify/not_change/reject | 过 |
| Q193 | backend·modify_batch | 做一下船租的敏感性分析，0.8、1、1.2三档 | clarify | clarify/not_change/reject | 过 |
| Q194 | backend·modify_batch | 这两种情况都算算：恶劣海况多和常态海况 | clarify | clarify/not_change/reject | 过 |
| Q200 | backend·modify_illegal | 最小化成本的平方 | reject | reject | 过 |
| Q201 | backend·modify_illegal | 风速乘以电价最小化 | reject | reject | 过 |
| Q202 | backend·modify_illegal | 碳排放最小化 | reject | reject | 过 |
| Q203 | backend·modify_illegal | 改一下叶片的颜色 | reject | reject | 过 |
| Q204 | backend·modify_illegal | 让任务018和022的距离最大 | reject | reject | 过 |
| Q205 | backend·modify_illegal | 把风险降低50% | reject | reject | 过 |
| Q206 | backend·modify_illegal | 明天的天气改好一点 | clarify | reject | route=clarify（要 ['reject']） |
| Q207 | backend·modify_illegal | 把这台风机删掉 | clarify | reject | route=clarify（要 ['reject']） |
| Q210 | backend·modify_constraint | 我今天累了，少干一点 | clarify | clarify | 过 |
| Q211 | backend·modify_constraint | 这周人手紧，先把严重故障处理掉，成本别太高 | dsl | clarify/dsl/not_change/reject | 过 |
| Q212 | backend·modify_constraint | 优先修高风险故障，预算别超过500万 | dsl | clarify | route=dsl（要 ['clarify']） |
| Q213 | backend·modify_constraint | 台风来了，先保安全，别管成本 | dsl | clarify | route=dsl（要 ['clarify']） |
| Q214 | backend·modify_constraint | 明天只有5个人能出海 | clarify | clarify | 过 |
| Q215 | backend·modify_constraint | 船坏了，只剩两条作业线 | dsl | dsl | 过 |
| Q220 | backend·modify_data | 11月3日风速15米每秒 | dsl | dsl | 过 |
| Q221 | backend·modify_data | 11月4日海况不可作业 | dsl | dsl | 过 |
| Q222 | backend·modify_data | 未来三天电价上涨20% | clarify | clarify | 过 |
| Q223 | backend·modify_data | 窗口02提前两天开始 | dsl | clarify/dsl/not_change/reject | 过 |
| Q230 | backend·modify_parameter | 候选开工粒度改成30分钟 | dsl | dsl | 过 |
| Q231 | backend·modify_parameter | 不允许跨天暂停 | dsl | dsl | 过 |
| Q232 | backend·modify_parameter | 安全风速上限改成11 | dsl | dsl | 过 |
| Q233 | backend·modify_parameter | 禁止夜间转移 | dsl | dsl | 过 |
| Q300 | backend·modify_parameter | 船租调整为8000元 | dsl | dsl | 过 |
| Q301 | backend·modify_parameter | 船舶日租金设为15000 | dsl | dsl | 过 |
| Q302 | backend·modify_parameter | 人工工资涨到200一小时 | dsl | dsl | 过 |
| Q303 | backend·modify_parameter | 把人工费降到100 | dsl | dsl | 过 |
| Q304 | backend·modify_parameter | 作业线最多开两条 | clarify | dsl | route=clarify（要 ['dsl']）；验收退回 A4 |
| Q305 | backend·modify_parameter | 只能开一条线 | dsl | dsl | 过 |
| Q306 | backend·modify_parameter | 人员控制在15人以内 | dsl | dsl | 过 |
| Q307 | backend·modify_parameter | 总人数不超过12 | dsl | dsl | 过 |
| Q308 | backend·modify_parameter | 每日工时改为6小时 | dsl | dsl | 过 |
| Q309 | backend·modify_parameter | 一天只能干5小时 | dsl | dsl | 过 |
| Q310 | backend·modify_parameter | 早上7点开工 | dsl | dsl | 过 |
| Q311 | backend·modify_parameter | 下午6点收工 | dsl | dsl | 过 |
| Q312 | backend·modify_parameter | 换风机留4小时路上时间 | dsl | dsl | 过 |
| Q313 | backend·modify_parameter | 把开工粒度调到15分钟 | dsl | dsl | 过 |
| Q314 | backend·modify_parameter | 允许跨天施工 | dsl | dsl | 过 |
| Q320 | backend·modify_data | 叶片备件11月6日到货2套 | dsl | dsl | 过 |
| Q321 | backend·modify_data | 轮毂备件11月4号才能到 | dsl | dsl | 过 |
| Q322 | backend·modify_data | 齿轮箱备件11月7日前到货 | dsl | dsl | 过 |
| Q323 | backend·modify_data | 主轴维修工时增加30% | dsl | dsl | 过 |
| Q324 | backend·modify_data | 发电机维修时间上涨15% | dsl | dsl | 过 |
| Q325 | backend·modify_data | 高严重任务11月9日前修完 | clarify | dsl | route=clarify（要 ['dsl']） |
| Q326 | backend·modify_data | 中严重11月10号前搞定 | clarify | dsl | route=clarify（要 ['dsl']）；验收退回 A7 |
| Q327 | backend·modify_data | 低严重任务11月12日前完成 | clarify | dsl | route=clarify（要 ['dsl']） |
| Q340 | backend·modify_constraint | 任务003和任务005不能安排在同一天 | dsl | dsl | 过 |
| Q341 | backend·modify_constraint | 003和005不要一起出海 | dsl | dsl | 过 |
| Q342 | backend·modify_constraint | 先修任务004然后修任务006 | dsl | dsl | 过 |
| Q343 | backend·modify_constraint | 任务004做完才轮到006 | dsl | dsl | 过 |
| Q344 | backend·modify_constraint | 任务007最早11月6日开工 | dsl | dsl | 过 |
| Q345 | backend·modify_constraint | 007不得早于11月6日 | dsl | dsl | 过 |
| Q346 | backend·modify_constraint | 任务008最晚11月9日前完成 | dsl | dsl | 过 |
| Q347 | backend·modify_constraint | 008必须在11月9号前修完 | dsl | dsl | 过 |
| Q348 | backend·modify_constraint | 任务009只安排1号船 | dsl | dsl | 过 |
| Q349 | backend·modify_constraint | 009固定在2号作业线 | dsl | dsl | 过 |
| Q350 | backend·modify_constraint | 11月7日禁止开工 | dsl | dsl | 过 |
| Q351 | backend·modify_constraint | 11月11号那天别施工 | dsl | dsl | 过 |
| Q352 | backend·modify_constraint | 任务010必须安排 | dsl | dsl | 过 |
| Q353 | backend·modify_constraint | 把任务011排除掉 | dsl | dsl | 过 |
| Q354 | backend·modify_constraint | 至少完成9项 | dsl | dsl | 过 |
| Q355 | backend·modify_constraint | 完成任务数不低于7 | dsl | dsl | 过 |
| Q356 | backend·modify_constraint | 总经济成本不超过250万 | dsl | dsl | 过 |
| Q357 | backend·modify_constraint | 现金支出控制在100万以内 | dsl | dsl | 过 |
| Q365 | backend·modify_objective | 按成本最低优先排 | dsl | dsl | 过 |
| Q366 | backend·modify_objective | 按最短完成优先排 | dsl | dsl | 过 |
| Q367 | backend·modify_objective | 按检修最多器件优先排 | dsl | dsl | 过 |
| Q368 | backend·modify_objective | 先修高危的 | dsl | dsl | 过 |
| Q369 | backend·modify_objective | 省钱第一 | dsl | dsl | 过 |
| Q370 | backend·modify_objective | 尽量多修几台 | dsl | dsl | 过 |
| Q371 | backend·modify_objective | 改成加权，高严重完成数0.5、总经济成本0.5 | reject | dsl | route=reject（要 ['dsl']） |
| Q372 | backend·modify_objective | 权重：高严重完成数0.7，最后完工时间0.3 | reject | dsl | route=reject（要 ['dsl']） |
| Q373 | backend·modify_objective | 先最小化最后完工时间，再最小化总经济成本 | dsl | dsl | 过 |
| Q380 | backend·modify_batch | 先按成本优先算一遍，再按风险优先算一遍，然后对比 | clarify | clarify/not_change/reject | 过 |
| Q381 | backend·modify_batch | 按船租0.8倍和1.2倍各算一版比较 | clarify | clarify/not_change/reject | 过 |
| Q382 | backend·modify_batch | 做两个场景：两条作业线和三条作业线 | clarify | clarify/not_change/reject | 过 |
| Q383 | backend·modify_batch | 先按默认算，再按不允许跨天算，比一比 | clarify | clarify/not_change/reject | 验收退回 A8 |
| Q390 | backend·modify_illegal | 最小化风速的平方 | reject | reject | 过 |
| Q391 | backend·modify_illegal | 让成本和工期相乘最小 | reject | reject | 过 |
| Q392 | backend·modify_illegal | 把二氧化碳排放降到最低 | reject | reject | 过 |
| Q393 | backend·modify_illegal | 把风机涂成蓝色 | reject | reject | 过 |
| Q394 | backend·modify_illegal | 最大化两台风机之间的距离 | reject | reject | 过 |
| Q395 | backend·modify_illegal | 删掉任务012 | reject | reject | 过 |
| Q396 | backend·modify_illegal | 把天气改成晴天 | reject | reject | 过 |
| Q397 | backend·modify_illegal | 任务013的风险概率乘以损失最小化 | reject | reject | 过 |
| Q400 | backend·modify_ambiguous | 明天风很大怎么办 | clarify | clarify | 过 |
| Q401 | backend·modify_ambiguous | 下周天气不好，排程要不要改 | clarify | clarify | 过 |
| Q402 | backend·modify_ambiguous | 帮我优化一下 | clarify | clarify | 过 |
| Q403 | backend·modify_ambiguous | 看着办吧 | clarify | clarify | 过 |
| Q404 | backend·modify_ambiguous | 尽量安排得合理一些 | clarify | clarify | 过 |
| Q405 | backend·modify_ambiguous | 明天只有3个人能出海 | clarify | clarify | 过 |
| Q406 | backend·modify_ambiguous | 如果有人请假怎么办 | not_change | clarify | route=not_change（要 ['clarify']） |
| Q407 | backend·modify_ambiguous | 船可能坏了，你看着调 | clarify | clarify | 过 |
| Q420 | backend·query | 现在库存够不够 | not_change | not_change | 过 |
| Q421 | backend·query | 窗口03什么时候开始 | not_change | not_change | 过 |
| Q422 | backend·query | 电价最高是哪天 | not_change | not_change | 过 |
| Q423 | backend·query | 完成率是多少 | not_change | not_change | 过 |
| Q424 | backend·query | 为什么任务005排在最后 | not_change | not_change | 过 |
| Q425 | backend·query | 这版和上版成本差多少 | not_change | not_change | 过 |
| Q426 | backend·query | 帮我解释一下目标函数 | not_change | not_change | 过 |
| Q427 | backend·query | 模型里有哪些约束 | not_change | not_change | 过 |
| Q428 | backend·query | 船租是怎么算的 | not_change | not_change | 过 |
| Q429 | backend·query | 哪个方案最省钱 | not_change | not_change | 过 |
| Q430 | backend·query | 未来五天海况怎么样 | not_change | not_change | 过 |
| Q431 | backend·query | 现在几个人可用 | not_change | not_change | 过 |
