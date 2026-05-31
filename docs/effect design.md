# Semipy Effect System — Design Analysis & Proposal

> **讀者提示**：本文件原為對話式技術回應，此版本加入了背景說明、術語解釋與設計理由，以便研究與實作參考。

---

## 術語速查 (Glossary)

| 術語 | 白話解釋 |
|------|---------|
| **Effect（效果/副作用）** | 函數除回傳值以外，對外部世界（DB、檔案、API）造成的任何改變 |
| **Pure Function（純函數）** | 只依賴輸入、只影響輸出，不碰外部世界的函數 |
| **Reification（具象化）** | 把「行為」變成「資料」——不是直接執行，而是先描述要做什麼 |
| **Effect Handler（效果處理器）** | 接收「描述」並決定如何真正執行的受信任元件 |
| **Provenance（出處）** | 可追溯某筆資料從哪個規格、哪次生成、哪段程式碼產生 |
| **Shadow World（影子世界）** | 一個隔離的沙盒環境（如 SQLite :memory:），用來安全地「試跑」效果 |
| **Event Sourcing（事件溯源）** | 以「事件日誌」為真相來源，資料庫狀態是日誌的投影 |

---

## 第一部分：診斷——semipy 今日的盲點

### 現況：Effect 是描述性的，且只看回傳值

`compute_effect_diff`（`contract/change.py`）目前的運作方式：

1. 對每個輸入樣本，記錄新舊實作的**回傳值 repr**
2. 比較差異，並用資料無關的不變量（invariants）來判斷好壞：`non_empty`、`non_identity`、`type_match`、`idempotent`……

這一切都建立在兩個**隱性假設**上：

- **假設 (a)**：函數的 effect 就是它的回傳值
- **假設 (b)**：函數是純函數（pure / deterministic）

### 問題的爆發點

當 AI 生成的程式碼執行了 `db.execute("UPDATE ...")` 時，兩個假設同時崩潰：

- 真正的 effect 在**外部世界**（資料庫），不在回傳值
- 執行完畢後，才能「發現」出錯——但已經**太遲了**

### 問題的二維分析

```
                 │  描述性（事後才發現它改變了）  │  規範性（程式在執行前就以資料宣告意圖）
─────────────────┼──────────────────────────────┼───────────────────────────────────────
主體 = 回傳值    │  ← semipy 今日的位置          │  —
─────────────────┼──────────────────────────────┼───────────────────────────────────────
主體 = 外部物件  │  天真延伸                      │  ★ 真正的槓桿點
（DB / 檔案/API）│  （快照前後差異）              │
```

**天真延伸**（快照 DB 前後、做 diff）仍屬於描述性：它告訴你 AI **已經**搞壞了什麼。

**真正的槓桿**在右下角：讓 effect 成為程式碼**發出（emit）**的第一類值，而非**執行（perform）**的動作——這樣就可以在它碰到世界之前，進行乾跑（dry-run）、審核、限制、還原，並記錄成可重播的歷史。

---

## 第二部分：核心手法——具象化效果，分離「決策」與「執行」

> **這是整份設計最關鍵的一個想法。所有後續章節都建立在這個基礎上。**

### 理論依據：代數效果與處理器

學術文獻（Plotkin & Pretnar 的代數效果；Leijen 的 Koka 語言）的核心洞見是：

> 計算不**執行** `Write`，它**發出** `Write` 操作——由外部的處理器來決定這個操作代表什麼。

生成的程式碼因此對一個 **effect 簽名**而言是純函數；解譯器（interpreter）是受信任的非 LLM 元件。

### 具體實作：能力物件（Capability Object）

在「有 effect 的模式」下，槽（slot）**不會**拿到真實的資料庫 handle，而是收到一個**能力物件（`fx`）**，它的方法只負責**記錄意圖**：

```python
# AI 生成的函數體——只發出 effects，不執行任何動作
def apply(row, fx):
    # > upsert 這個客戶，然後將舊記錄標為已取代
    fx.upsert("customers", key=row["id"], value=normalize(row))
    fx.update("customers_history",
              where={"id": row["id"]},
              set={"status": "superseded"})
    return fx.script  # 回傳一份 EffectScript（意圖清單），而非執行結果
```

`Effect` 是純資料：`{op, target, payload, compensation?, provenance}`

**關鍵安全性質**：LLM 字面上**無法觸及**資料庫——它只能描述它想要做什麼。

這個設計同時是安全邊界，也是生成約束，而 semipy 已經擁有 gist 環境與 agent tool surface，可以直接強制執行。

---

## 第三部分：「免費」的版本控制——Effect Ledger 作為投影來源

### 核心洞見：事件溯源（Event Sourcing）

一旦 effects 被具象化，**事件溯源**幾乎是白送的版本控制：

- 不可附加（append-only）的 effects 日誌 = 歷史記錄
- 資料庫的當前狀態 = 日誌的投影（projection）

### 設計：與現有 Slot DAG 共同版本化

不要讓 Ledger 成為孤立的儲存。讓它與 semipy 既有的 slot DAG 共同版本化：

```
EffectEvent {
  event_id,
  slot_id,
  origin_commit_id,    # ← 連結回程式碼版本
  contract_case_ids[], # ← 連結回測試案例（「為什麼」）
  applied_effects[],
  compensation[],
  artifact_snapshot_ref,
  status
}
```

### 雙維版本控制系統

這個設計讓 semipy 成為**雙維**的版本控制系統：

```
實作軸（現有的 Merkle DAG）
  → 生成程式碼的演進歷程（GENERATE / ADAPT / REUSE）

物件軸（新的 Ledger）
  → 世界狀態的演進歷程（每次對 DB 的實際改變）

出處邊（Provenance edges）
  → 跨軸交叉引用
```

### 獨特的出處鏈（Provenance Chain）

指向物件中的任何一列資料，可以往回追溯：

```
某列資料
  ↓
EffectEvent（發生了什麼）
  ↓
Contract Case（為什麼要這樣做——用自然語言寫的理由）
  ↓
Implementation Commit（如何做到——生成的程式碼）
  ↓
Slot Spec（使用者的 #> 規格說明）
```

> 沒有其他系統擁有「自然語言規格 → 物件資料格」這條連結，因為沒有其他系統像 semipy 的合約那樣，將生成程式碼的演進歷史與其**意圖**放在一起。這是 semipy 的**獨特貢獻**，而且已經半完成了——`ChangeRecord` 已攜帶 `reason`、`decision`、`origin_commit_id`，只需要將它從「輸出改變了」擴展到「世界改變了」。

### Effect 的版本控制操作

| 操作 | 實作方式 |
|------|---------|
| **還原（Revert）** | 從 Ledger 重播儲存的 compensations |
| **審計（Audit）** | 走訪出處鏈 |
| **時光旅行（Time-travel）** | 重建投影到事件 N |

---

## 第四部分：為什麼 semipy 是理想宿主

> 這裡的重點是**量身打造**，而非拼裝通用工具。

### (a) 資料無關的不變量詞彙 → Effect 不變量詞彙

semipy 已有一套固定、資料無關的不變量（`non_empty`、`idempotent` 等）。  
以相同哲學，新增 **effect 類型**的案例：

| Effect 不變量 | 含義 |
|--------------|------|
| `append_only` | 永不刪除或覆蓋現有列 |
| `bounded_blast_radius` | 最多影響 N 列，或只影響已宣告的目標（防止 LLM「隨意亂改很多東西」）|
| `idempotent_effect` | 重複套用 EffectScript 是 no-op |
| `reversible` | 存在 compensation，且能在影子世界中 round-trip |

這些直接插入合約，作為新的 `CaseKind`，並以現有的兩個閘道（gate）同樣的方式進行門控——只是閘道現在針對**影子物件**執行 EffectScript。

### (b) 子程序 gist → Worlds 式的影子世界

Warth & Kay 的 **Worlds**（控制副作用範圍——類似瀏覽器分頁，可 commit 或 discard 的子世界）是暫存（staging）的精確基礎。

semipy 已在隔離的子程序 gist 中執行候選程式碼以進行合約檢查；那就是 EffectScript 對影子世界執行的地方：

- SQLite `:memory:` 複製
- 或在真實的事務型儲存上執行 `BEGIN; …; ROLLBACK`

只有在 effect 合約閘道通過後，影子世界才 commit 到真實物件。

### (c) `compute_effect_diff` 從輸出差異 → 物件差異

現有的 effect-diff 對合約輸入執行新舊實作，並以結構指紋分類 intended / unintended。

**提升主體**：在影子世界中執行兩個實作的 EffectScript，比較**結果物件狀態**（而非回傳值的 repr）。

「非預期」的含義變成：「新實作影響了父版本沒有影響的列」——這是**爆炸半徑回歸（blast-radius regression）**，由 `contract_block_regressions` 門控。

去重（dedup by fingerprint）機制不需改變。

---

## 第五部分：誠實面對困難——Effect 頻譜與 semipy 特有風險

### Effect 頻譜

並非所有事物都能被遮蔽（shadow），Effects 存在於一個頻譜上：

#### 可緩衝 / 可內化（Bufferable）
**例子**：事務型 DB 寫入、檔案寫入

**完整治療方案**：影子、差異、閘道、原子地 commit 或 discard（Atomix 2026 的「可緩衝效果可以延遲」案例）

#### 已外部化 / 不可逆（Externalized / Irreversible）
**例子**：發郵件、扣款、第三方 POST

**無法**真正遮蔽或補償。三個傳統防禦：

1. **乾跑（Dry-run）**：「以下是我將要做的事」+ 人工審核後，handler 才外部化
2. **必要的補償宣告**：依照 Sagas（Garcia-Molina & Salem），handler 拒絕執行不攜帶補償方案的不可逆效果
3. **冪等鍵（Idempotency keys）**：防止重複執行

### semipy 特有的尖銳風險：語義回滾攻擊

> **ACRFence（2026）** 的發現：當 agent 在 restore 後重新合成呼叫時，重試的呼叫**不會**與原版相同。

semipy 因為會**重新生成實作**（ADAPT）而特別暴露在這個風險下：

- 在 commit A 下生成的 effect，不保證能由 commit B 重現

**設計含義是決定性的**：

```
Ledger 必須儲存「已套用效果的物化資料」（實際發生了什麼）
                        ≠
從 impl DAG 重新推導的計劃（我們現在會怎麼做）
```

這就是為什麼「只要版本控制程式碼」的方法無法捕捉 effects——以及為什麼兩個軸必須是**分開儲存、交叉連結**的。

---

## 第六部分：分階段、向後相容的採用路徑

> **純函數的槽保持不變（空 EffectScript），不會破壞任何現有功能。**

### Stage 0 — 表示層（Representation）
**目標**：建立資料結構與介面

- 為 slot 加入 `effect_mode`
- 注入 `fx` 能力物件
- 生成的程式碼回傳 `EffectScript`
- 在 `agents/generator` 中交付能力工具界面

### Stage 1 — 影子與閘道（Shadow + Gate）
**目標**：讓 effects 可被審核與門控

- 在影子世界中執行 EffectScript
- 加入 effect 不變量：`append_only` / `bounded_blast_radius` / `idempotent_effect` / `reversible`
- 透過現有閘道鉤（gate hooks）進行門控

### Stage 2 — 物件 Effect 差異（Artifact Effect-Diff）
**目標**：比較新舊版本對世界的影響

- 將 `compute_effect_diff` 的主體重新指向**影子物件狀態**（而非回傳值 repr）
- 「非預期」= 新實作影響了父版本未影響的列（爆炸半徑回歸）

### Stage 3 — Ledger 與出處（Ledger + Provenance）
**目標**：建立完整的版本歷史

- 將已套用的 effects 以 `commit_id` 為鍵附加到 Ledger
- 物件 = 投影；暴露出處鏈與 `revert(event_id)` 於 `portal_inspect`

### Stage 3.5 — 外部化效果（Externalized Effects）
**目標**：處理不可逆操作

- 不可遮蔽操作的審核 / 必要補償 / 冪等鍵政策

---

## 第七部分：研究框架

可發表的論文主軸不是「我們加了事件溯源」，而是：

> **為 LLM 合成程式碼的共同版本化實作與效果出處** —— 一個單一系統，透過版本化、可再生的生成實作及其行為合約，將自然語言規格連結到真實世界物件變更的不可附加日誌，並在 commit 前在分階段世界中以 effect 層級不變量進行門控。

### 與現有文獻的定位

| 比較對象 | 現有研究做了什麼 | semipy 的新貢獻 |
|---------|--------------|---------------|
| **代數效果/處理器** | 為人工編寫的程式碼具象化 effects | 生產者是 LLM；處理器同時也是安全/合約邊界 |
| **事件溯源 / 出處** | 版本化固定程式碼的狀態或追蹤血緣 | 生成 effect 的程式碼本身也是版本化且可變的；Ledger-to-impl 連結是多對一隨時間變化，且必須在重新生成後存活（ACRFence 風險）|
| **Sagas / Atomix** | 處理給定動作的事務性 | 這些動作是被合成的，並在納入前針對學習、累積的合約進行門控 |

**這個交叉點——PL 效果系統 × 資料庫出處 × agent 動作安全，由規格到物件的合約統一——是目前的開放研究領域。**

---

## 待確認事項（給作者的問題）

在制定具體計劃之前，需要確認三個方向：

**Q1. 「物件（artifact）」的範圍**  
動機目標是否特指關聯式 DB（事務型影子 + SQL-diff 最整潔），還是希望從第一天起就讓抽象層同時涵蓋檔案 / 外部 API？  
→ 前者是緊湊、可展示的切片；後者會提早強制引入外部化效果機制。

**Q2. 信任邊界的位置**  
是否可以接受限制生成方式，使 LLM 只能透過 `fx` 發出效果（最強保證，但需改變生成提示詞/工具界面）？  
還是要先採用較軟的方式：允許生成的程式碼呼叫 DB，但以影子事務 + diff 包裹它（較容易，但限制較弱）？

**Q3. 合約 vs. Ledger 的重心**  
當前的研究興趣主要是**閘道**（在 commit 前阻止壞的 effects），還是**版本控制 / 出處**（在發生後追蹤與還原 effects）？  
→ 兩者共享具象化基礎，但原型開發的順序不同。

---

## 參考文獻

- Warth, Ohshima, Kaehler, Kay — *Worlds: Controlling the Scope of Side Effects* (ECOOP 2011)
- Plotkin & Pretnar — *Handling Algebraic Effects*
- Leijen — *Algebraic Effects for Functional Programming* (Koka)
- Garcia-Molina & Salem — *Sagas* (SIGMOD 1987)
- Cheney, Chiticariu, Tan — *Provenance in Databases: Why, How, and Where*
- Buneman, Khanna, Tan — *Why and Where: A Characterization of Data Provenance*
- Microsoft Azure Architecture Center — *Event Sourcing Pattern*
- Atomix — *Timely, Transactional Tool Use for Reliable Agentic Workflows* (2026)
- ACRFence — *Preventing Semantic Rollback Attacks in Agent Checkpoint-Restore* (2026)
- *Towards Verifiably Safe Tool Use for LLM Agents*