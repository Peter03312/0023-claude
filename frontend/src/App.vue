<script setup>
import { computed, reactive, ref } from 'vue'

const form = reactive({
  start: '000001',
  direction: 'asc',
  copies: 1,
  sheetCount: 10,
  spoilText: '',
  checkFrom: '',
  checkTo: '',
})

const actuals = ref([])
const fieldErrors = reactive({})
const formError = ref('')
const loading = ref(false)
const result = ref(null)

const spoilSet = computed(() => new Set(parsedSpoils.value))
const parsedSpoils = ref([])

// 后端 field（snake_case）到表单字段的映射，用于把拒绝原因落到对应输入处
const FIELD_KEY_MAP = {
  start: 'start',
  direction: 'direction',
  copies: 'copies',
  sheet_count: 'sheetCount',
  spoil_sheets: 'spoilText',
  check_from: 'checkFrom',
  check_to: 'checkTo',
  actuals: 'actuals',
}

function parseSpoils() {
  const text = form.spoilText.trim()
  if (!text) return []
  return text
    .split(/[\s,，、;；]+/)
    .filter(Boolean)
    .map((token) => Number(token))
}

// 核对区间（闭区间，纸序为计划中的绝对序号）；留空表示覆盖计划全程
const rangeInfo = computed(() => {
  const n = Number(form.sheetCount)
  const from = form.checkFrom === '' ? 1 : Number(form.checkFrom)
  const to = form.checkTo === '' ? n : Number(form.checkTo)
  const valid =
    Number.isInteger(n) && n >= 1 && n <= 200 &&
    Number.isInteger(from) && Number.isInteger(to) &&
    from >= 1 && to <= n && from <= to
  return { from, to, length: valid ? to - from + 1 : 0, valid }
})

// 区间非法时实测区仍按第 1 张起标注，避免显示 NaN
const displayFrom = computed(() => (rangeInfo.value.valid ? rangeInfo.value.from : 1))

function isPlannedSpoil(sheetNo) {
  return spoilSet.value.has(sheetNo)
}

// 纸张数或核对区间变化时同步实测行数；计划废张行自动预填 SPOIL（可手动覆盖）
function syncActuals() {
  const info = rangeInfo.value
  if (!info.valid) return
  const next = actuals.value.slice(0, info.length)
  for (let i = next.length; i < info.length; i++) {
    next.push(isPlannedSpoil(info.from + i) ? 'SPOIL' : '')
  }
  actuals.value = next
}

function onSpoilTextChange() {
  parsedSpoils.value = parseSpoils()
  for (let i = 0; i < actuals.value.length; i++) {
    if (isPlannedSpoil(displayFrom.value + i) && actuals.value[i].trim() === '') {
      actuals.value[i] = 'SPOIL'
    }
  }
}

function validateLocally() {
  for (const key of Object.keys(fieldErrors)) delete fieldErrors[key]
  formError.value = ''

  if (!/^\d{6}$/.test(form.start)) {
    fieldErrors.start = '起号必须是恰好六位数字。'
  }
  if (![1, 2, 3].includes(Number(form.copies))) {
    fieldErrors.copies = '每号有效印次为 1~3。'
  }
  const n = Number(form.sheetCount)
  if (!Number.isInteger(n) || n < 1 || n > 200) {
    fieldErrors.sheetCount = '计划纸张数为 1~200。'
  }

  const tokens = (form.spoilText.trim() ? form.spoilText.split(/[\s,，、;；]+/).filter(Boolean) : [])
  const values = []
  for (const token of tokens) {
    const v = Number(token)
    if (!/^\d+$/.test(token) || !Number.isInteger(v)) {
      fieldErrors.spoilText = `废张序号「${token}」不是整数。`
      break
    }
    if (v < 1 || v > n) {
      fieldErrors.spoilText = `废张序号 ${v} 超出 1~${n}。`
      break
    }
    values.push(v)
  }
  if (!fieldErrors.spoilText) {
    for (let i = 1; i < values.length; i++) {
      if (values[i] === values[i - 1]) {
        fieldErrors.spoilText = `废张序号 ${values[i]} 重复。`
        break
      }
      if (values[i] < values[i - 1]) {
        fieldErrors.spoilText = `废张序号必须严格升序：${values[i]} 在 ${values[i - 1]} 之前。`
        break
      }
    }
  }
  if (!fieldErrors.spoilText && values.length === n) {
    fieldErrors.spoilText = '全部纸张都是废张，无法形成号码轨迹。'
  }

  // 核对区间：留空默认覆盖全程；填写则须为计划范围内的整数且起不晚于止
  const fromVal = form.checkFrom === '' ? 1 : Number(form.checkFrom)
  const toVal = form.checkTo === '' ? n : Number(form.checkTo)
  const nValid = Number.isInteger(n) && n >= 1 && n <= 200
  if (!Number.isInteger(fromVal) || fromVal < 1 || (nValid && fromVal > n)) {
    fieldErrors.checkFrom = `核对起始纸序须为 1~${nValid ? n : 200} 的整数（留空默认第 1 张）。`
  }
  if (!Number.isInteger(toVal) || toVal < 1 || (nValid && toVal > n)) {
    fieldErrors.checkTo = `核对结束纸序须为 1~${nValid ? n : 200} 的整数（留空默认第 ${nValid ? n : '末'} 张）。`
  }
  if (!fieldErrors.checkFrom && !fieldErrors.checkTo && fromVal > toVal) {
    fieldErrors.checkFrom = '核对起始纸序不能大于核对结束纸序。'
  }

  if (rangeInfo.value.valid && actuals.value.length !== rangeInfo.value.length) {
    formError.value = `实测行数（${actuals.value.length}）与核对区间长度（${rangeInfo.value.length}）不一致。`
  }

  return Object.keys(fieldErrors).length === 0 && !formError.value
}

async function submit() {
  result.value = null
  if (!validateLocally()) return

  const payload = {
    start: form.start,
    direction: form.direction,
    copies: Number(form.copies),
    sheet_count: Number(form.sheetCount),
    spoil_sheets: parseSpoils(),
    check_from: rangeInfo.value.from,
    check_to: rangeInfo.value.to,
    actuals: actuals.value.map((v) => v.trim()),
  }

  loading.value = true
  try {
    const resp = await fetch('/api/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    const body = await resp.json()
    if (!resp.ok) {
      // 后端 field 落到对应输入处；当前录入内容保留，便于修正后重提
      const key = body.field && FIELD_KEY_MAP[body.field]
      if (key) {
        fieldErrors[key] = body.error
      } else {
        formError.value = body.error || '请求被拒绝。'
      }
      return
    }
    result.value = body
  } catch (err) {
    formError.value = `无法连接后端核对服务：${err.message}`
  } finally {
    loading.value = false
  }
}

// 初始化
parsedSpoils.value = parseSpoils()
syncActuals()
</script>

<template>
  <main class="page">
    <h1>凸版号码机走票核对</h1>
    <p class="hint">
      录入试走计划与逐张实测号码。第一个非废张使用起号；同号压满规定印次后，
      <strong>下一张非废张</strong>才按方向变化一号；废张固定为
      <code>SPOIL</code>，不换号也不消耗有效印次。
    </p>

    <section class="card">
      <h2>1. 走票计划</h2>
      <div class="grid">
        <label>
          六位起号
          <input v-model="form.start" maxlength="6" inputmode="numeric"
                 :class="{ bad: fieldErrors.start }" placeholder="000001" />
          <span v-if="fieldErrors.start" class="err">{{ fieldErrors.start }}</span>
        </label>

        <label>
          方向
          <select v-model="form.direction">
            <option value="asc">递增（asc）</option>
            <option value="desc">递减（desc）</option>
          </select>
        </label>

        <label>
          每号有效印次
          <select v-model.number="form.copies">
            <option :value="1">1 次</option>
            <option :value="2">2 次</option>
            <option :value="3">3 次</option>
          </select>
          <span v-if="fieldErrors.copies" class="err">{{ fieldErrors.copies }}</span>
        </label>

        <label>
          计划纸张数（1~200）
          <input v-model.number="form.sheetCount" type="number" min="1" max="200"
                 :class="{ bad: fieldErrors.sheetCount }"
                 @change="syncActuals" />
          <span v-if="fieldErrors.sheetCount" class="err">{{ fieldErrors.sheetCount }}</span>
        </label>

        <label class="wide">
          计划废张序号（1 起，严格升序、不重复，逗号/空格分隔）
          <input v-model="form.spoilText" :class="{ bad: fieldErrors.spoilText }"
                 placeholder="例如：4, 9"
                 @change="onSpoilTextChange" />
          <span v-if="fieldErrors.spoilText" class="err">{{ fieldErrors.spoilText }}</span>
        </label>
      </div>
    </section>

    <section class="card">
      <h2>2. 核对区间与逐张实测（六位号码或 SPOIL）</h2>
      <div class="grid">
        <label>
          核对起始纸序（留空默认第 1 张）
          <input v-model="form.checkFrom" type="number" min="1" :max="form.sheetCount"
                 :class="{ bad: fieldErrors.checkFrom }" placeholder="1"
                 @change="syncActuals" />
          <span v-if="fieldErrors.checkFrom" class="err">{{ fieldErrors.checkFrom }}</span>
        </label>

        <label>
          核对结束纸序（留空默认第 {{ form.sheetCount }} 张）
          <input v-model="form.checkTo" type="number" min="1" :max="form.sheetCount"
                 :class="{ bad: fieldErrors.checkTo }" :placeholder="String(form.sheetCount)"
                 @change="syncActuals" />
          <span v-if="fieldErrors.checkTo" class="err">{{ fieldErrors.checkTo }}</span>
        </label>
      </div>
      <p v-if="rangeInfo.valid" class="hint range-hint">
        本次核对第 {{ rangeInfo.from }} ~ {{ rangeInfo.to }} 张，共 {{ rangeInfo.length }} 张；
        请按此区间逐张录入实测，结果纸序为计划中的绝对序号。
      </p>
      <div class="actual-grid">
        <label v-for="(_, i) in actuals" :key="i" class="actual-cell"
               :class="{ spoil: isPlannedSpoil(displayFrom + i) }">
          <span class="sheet-tag">第 {{ displayFrom + i }} 张<template v-if="isPlannedSpoil(displayFrom + i)"> · 废</template></span>
          <input v-model="actuals[i]" :aria-label="`第 ${displayFrom + i} 张实测`"
                 :class="{ bad: fieldErrors.actuals }"
                 :placeholder="isPlannedSpoil(displayFrom + i) ? 'SPOIL' : '000000'" />
        </label>
      </div>
      <span v-if="fieldErrors.actuals" class="err">{{ fieldErrors.actuals }}</span>
    </section>

    <div class="actions">
      <button :disabled="loading" @click="submit">
        {{ loading ? '核对中…' : '生成轨迹并核对' }}
      </button>
      <span v-if="formError" class="err form-err">{{ formError }}</span>
    </div>

    <section v-if="result" class="card result">
      <h2>3. 核对结果</h2>
      <p class="hint range-hint">
        核对区间：第 {{ result.check_from }} ~ {{ result.check_to }} 张
        （纸序为计划中的绝对序号）。
      </p>
      <div :class="result.all_match ? 'banner ok' : 'banner fail'">
        <template v-if="result.all_match">
          ✅ 全部 {{ result.rows.length }} 张一致，轨迹无误。
        </template>
        <template v-else>
          ❌ 共 {{ result.mismatch_sheets.length }} 张不匹配，
          <strong>首个错号在第 {{ result.first_mismatch_sheet }} 张</strong>
          （差异纸序：{{ result.mismatch_sheets.join('、') }}）。
          请重点确认废张之后是否仍按原号码续印。
        </template>
      </div>

      <table>
        <thead>
          <tr>
            <th>纸序</th>
            <th>类型</th>
            <th>当前号码</th>
            <th>有效印次进度</th>
            <th>期望</th>
            <th>实测</th>
            <th>判定 / 原因</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in result.rows" :key="row.sheet_no"
              :class="{ mismatch: !row.match, spoilrow: row.is_spoil }">
            <td>{{ row.sheet_no }}</td>
            <td>{{ row.is_spoil ? '计划废张' : '有效压印' }}</td>
            <td class="mono">{{ row.number }}</td>
            <td>{{ row.impression }}</td>
            <td class="mono">{{ row.expected }}</td>
            <td class="mono">{{ row.actual }}</td>
            <td>
              <span v-if="row.match" class="ok-text">一致</span>
              <span v-else class="err">{{ row.mismatch_reason }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </main>
</template>
