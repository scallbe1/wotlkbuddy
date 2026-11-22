-- WotLKBuddy v0.3 read-only client telemetry; compatible with WoW 3.3.5a Lua 5.1.
-- Exports a checksum-validated, versioned byte stream as RGB pixels at the top-left.
-- Does not execute movement, combat, chat, quests, or privileged APIs.

local SIDE = 4
local COLS = 48
local ROWS = 20
local CAPACITY = (COLS * ROWS - 6) * 3
local MAGIC = {{255, 0, 255}, {0, 255, 255}, {255, 255, 0}, {0, 0, 0}}
local canvas = CreateFrame('Frame', 'WotLKBuddyTelemetryFrame', UIParent)
canvas:SetWidth(COLS * SIDE)
canvas:SetHeight(ROWS * SIDE)
canvas:SetScale(1 / UIParent:GetEffectiveScale())
canvas:SetPoint('TOPLEFT', UIParent, 'TOPLEFT', 12, -12)
canvas:SetFrameStrata('TOOLTIP')
canvas:EnableMouse(false)

local pixels = {}
local previous = {}
for n = 1, COLS * ROWS do
    local texture = canvas:CreateTexture(nil, 'ARTWORK')
    texture:SetWidth(SIDE)
    texture:SetHeight(SIDE)
    local col = (n - 1) % COLS
    local row = math.floor((n - 1) / COLS)
    texture:SetPoint('TOPLEFT', canvas, 'TOPLEFT', col * SIDE, -row * SIDE)
    texture:SetTexture(0, 0, 0, 1)
    pixels[n] = texture
end

local function setPixel(index, r, g, b)
    local old = previous[index]
    if old and old[1] == r and old[2] == g and old[3] == b then return end
    pixels[index]:SetTexture(r / 255, g / 255, b / 255, 1)
    previous[index] = {r, g, b}
end

local function safe(value, maxLength)
    if value == nil then return '' end
    local s = tostring(value)
    s = string.gsub(s, '[\r\n\t]', ' ')
    return string.sub(s, 1, maxLength or 100)
end

-- WotLK 3.3.5 completion history is a server response. Query it rather than
-- assuming an absent journal entry means a completed quest.
local completionCache = nil
local lastCompletionQuery = 0
local function queryCompletions()
    if QueryQuestsCompleted and (GetTime() - lastCompletionQuery >= 1) then
        lastCompletionQuery = GetTime()
        pcall(QueryQuestsCompleted)
    end
end
local questHistoryFrame = CreateFrame('Frame')
questHistoryFrame:RegisterEvent('QUEST_QUERY_COMPLETE')
questHistoryFrame:RegisterEvent('QUEST_FINISHED')
questHistoryFrame:RegisterEvent('QUEST_LOG_UPDATE')
questHistoryFrame:RegisterEvent('PLAYER_ENTERING_WORLD')
questHistoryFrame:SetScript('OnEvent', function(_, event)
    if event == 'QUEST_QUERY_COMPLETE' then
        if GetQuestsCompleted then
            local ok, flags = pcall(GetQuestsCompleted)
            if ok and type(flags) == 'table' then completionCache = flags end
        end
    else
        queryCompletions()
    end
end)

local function tickData()
    local lines = {}
    local isTruncated = false
    local payloadBytes = 0
    -- Always reserve room for a terminal truncation marker.
    local function appendLine(line)
        if payloadBytes + string.len(line) + 1 > CAPACITY - 20 then
            isTruncated = true
            return false
        end
        table.insert(lines, line)
        payloadBytes = payloadBytes + string.len(line) + 1
        return true
    end
    local hp = UnitHealth('player') or 0
    local maxhp = UnitHealthMax('player') or 0
    local px, py = GetPlayerMapPosition('player')
    local guid = UnitGUID('player') or ''
    local zone = GetZoneText() or ''
    local combat = UnitAffectingCombat('player') and 1 or 0
    local dead = UnitIsDeadOrGhost('player') and 1 or 0
    local target = UnitExists('target') and (UnitGUID('target') or '') or ''
    local playerLine = table.concat({
        'P', safe(UnitName('player'), 50), safe(guid, 32),
        tostring(UnitLevel('player') or 0), tostring(hp), tostring(maxhp),
        tostring(combat), tostring(dead),
        string.format('%.5f', px or 0), string.format('%.5f', py or 0),
        tostring(GetCurrentMapContinent() or 0), tostring(GetCurrentMapZone() or 0),
        safe(zone, 55), safe(target, 32),
        safe(UnitName('target'), 50),
    }, '\t')
    appendLine(playerLine)
    -- Optional status extension: no new protocol version is required. If the
    -- status line cannot fit, the existing truncation marker prevents guessing.
    local mana = UnitMana('player') or 0
    local maxmana = UnitManaMax('player') or 0
    local free, capacity = 0, 0
    for bag = 0, 4 do
        local slots = GetContainerNumSlots(bag) or 0
        capacity = capacity + slots
        for slot = 1, slots do
            local texture = GetContainerItemInfo(bag, slot)
            if not texture then free = free + 1 end
        end
    end
    -- Some WotLK builds may not expose durability on all equip slots.
    -- Report -1 when unavailable instead of assuming equipment is healthy.
    local durability = -1
    if GetInventoryItemDurability then
        for slot = 1, 19 do
            local now, maximum = GetInventoryItemDurability(slot)
            if now and maximum and maximum > 0 then
                local percent = math.floor(100 * now / maximum + 0.5)
                if durability < 0 or percent < durability then
                    durability = percent
                end
            end
        end
    end
    local talents = -1
    if UnitCharacterPoints then
        talents = UnitCharacterPoints('player') or -1
    end
    local _, classToken = UnitClass('player')
    local _, raceToken = UnitRace('player')
    appendLine(table.concat({'S', tostring(mana), tostring(maxmana),
        tostring(free), tostring(capacity), tostring(durability),
        tostring(talents), tostring(GetMoney() or 0),
        safe(classToken, 18), safe(raceToken, 18)}, '\t'))
    -- Read-only NPC dialogue state. GetQuestID() reads the open quest frame,
    -- not the quest log; 0 means the client did not expose a quest ID.
    local dialog = 'none'
    if QuestFrame and QuestFrame:IsShown() then
        if QuestFrameDetailPanel and QuestFrameDetailPanel:IsShown() then
            dialog = 'detail'
        elseif QuestFrameProgressPanel and QuestFrameProgressPanel:IsShown() then
            dialog = 'progress'
        elseif QuestFrameRewardPanel and QuestFrameRewardPanel:IsShown() then
            dialog = 'reward'
        end
    elseif GossipFrame and GossipFrame:IsShown() then
        dialog = 'gossip'
    end
    local dialogQuestID = 0
    if (dialog == 'detail' or dialog == 'progress' or dialog == 'reward') and GetQuestID then
        dialogQuestID = tonumber(GetQuestID()) or 0
    end
    -- On some WotLK 3.3.5 clients GetQuestID() returns 0 while the quest
    -- detail frame is visible. GetTitleText() is supplied by the original
    -- QuestFrame UI and identifies the displayed quest by its visible title.
    -- Only include the title when a quest panel is actually visible; never
    -- reuse stale text outside a quest frame.
    local dialogQuestTitle = ''
    if (dialog == 'detail' or dialog == 'progress' or dialog == 'reward') and GetTitleText then
        dialogQuestTitle = safe(GetTitleText(), 100)
    end
    local rewardChoices, moneyRequired = -1, -1
    if dialog == 'reward' then
        if GetNumQuestChoices then rewardChoices = GetNumQuestChoices() or -1 end
        if GetQuestMoneyToGet then moneyRequired = GetQuestMoneyToGet() or 0 end
    end
    appendLine(table.concat({'I', dialog, tostring(dialogQuestID), dialogQuestTitle,
        tostring(rewardChoices), tostring(moneyRequired)}, '\t'))
    local completed = -1
    if IsQuestFlaggedCompleted then
        local ok, value = pcall(IsQuestFlaggedCompleted, 363)
        if ok and value ~= nil then completed = value and 1 or 0 end
    elseif completionCache then
        completed = completionCache[363] and 1 or 0
    end
    appendLine(table.concat({'C', '363', tostring(completed)}, '\t'))
    local entries = GetNumQuestLogEntries() or 0
    for i = 1, entries do
        local title, level, _, _, isHeader, _, isComplete, _, questID = GetQuestLogTitle(i)
        if not isHeader and questID and questID > 0 then
            local qid = tostring(questID)
            local complete = (isComplete == 1) and '1' or '0'
            if not appendLine(table.concat({'Q', qid, tostring(level or 0), complete,
                safe(title, 70)}, '\t')) then break end
            local objectiveCount = GetNumQuestLeaderBoards(i) or 0
            for j = 1, objectiveCount do
                local description, kind, finished = GetQuestLogLeaderBoard(j, i)
                if not appendLine(table.concat({'O', qid, tostring(j),
                    finished and '1' or '0', safe(kind, 20), safe(description, 90)}, '\t')) then
                    break
                end
            end
        end
    end
    if isTruncated then table.insert(lines, 'E\tTRUNCATED') end
    return table.concat(lines, '\n') .. '\n'
end

local sequence = 0
local function publish()
    local ok, data = pcall(tickData)
    if not ok then
        data = 'E\tLUA_ERROR\t' .. safe(data, 80) .. '\n'
    end
    if string.len(data) > CAPACITY then data = 'E\tPAYLOAD_TOO_LONG\n' end
    local length = string.len(data)
    local checksum = 0
    for i = 1, length do
        checksum = (checksum + i * string.byte(data, i)) % 65536
    end
    sequence = (sequence + 1) % 256
    for i = 1, 4 do setPixel(i, unpack(MAGIC[i])) end
    setPixel(5, 3, length % 256, math.floor(length / 256))
    setPixel(6, sequence, checksum % 256, math.floor(checksum / 256))
    local index = 7
    for i = 1, length, 3 do
        setPixel(index, string.byte(data, i) or 0,
            string.byte(data, i + 1) or 0, string.byte(data, i + 2) or 0)
        index = index + 1
    end
    -- Clear unused cells so old payload cannot be misinterpreted as current data.
    while index <= COLS * ROWS do
        setPixel(index, 0, 0, 0)
        index = index + 1
    end
end

local elapsedTime = 0
canvas:SetScript('OnUpdate', function(_, elapsed)
    elapsedTime = elapsedTime + elapsed
    if elapsedTime < 0.5 then return end
    elapsedTime = 0
    publish()
end)

SLASH_WOTLKBUDDY1 = '/wbt'
SlashCmdList['WOTLKBUDDY'] = function(msg)
    if msg == 'hide' then
        canvas:Hide()
        DEFAULT_CHAT_FRAME:AddMessage('WotLKBuddy telemetry hidden (/wbt show to resume).')
    elseif msg == 'show' then
        canvas:Show()
        DEFAULT_CHAT_FRAME:AddMessage('WotLKBuddy telemetry visible.')
    else
        DEFAULT_CHAT_FRAME:AddMessage('WotLKBuddy telemetry: /wbt show or /wbt hide')
    end
end

queryCompletions()
publish()
