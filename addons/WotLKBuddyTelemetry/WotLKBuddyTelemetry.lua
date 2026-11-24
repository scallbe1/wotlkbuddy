-- WotLKBuddy v0.3 read-only client telemetry; compatible with WoW 3.3.5a Lua 5.1.
-- Exports a checksum-validated, versioned byte stream as RGB pixels above center-right.
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
-- Position away from lower-left action bars/chat and upper-right minimap/tooltips.
-- A fixed horizontal offset from screen centre keeps the grid within the viewport.
canvas:ClearAllPoints()
canvas:SetPoint('TOP', UIParent, 'TOP', 220, -20)
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

-- Verify the actual 1-0 key -> main action-button association.
-- GetBindingKey may report two alternatives; GetBindingAction is a
-- separate read-only lookup when available. Neither guesses WoW settings.
-- BEGIN ACTIONBAR BINDING HELPER
local function slotHasDigitBinding(slot)
    local key = slot == 10 and '0' or tostring(slot)
    local action = 'ACTIONBUTTON' .. tostring(slot)
    if GetBindingAction and GetBindingAction(key) == action then
        return true
    end
    if GetBindingKey then
        local first, second = GetBindingKey(action)
        return first == key or second == key
    end
    return false
end

-- END ACTIONBAR BINDING HELPER

-- WotLK 3.3.5 completion history is a server response. Query it rather than
-- assuming an absent journal entry means a completed quest.
local completionCache = nil
local recentlyObservedQuestIds = {}
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

-- The controller can request ONE exact quest-item inventory count. The
-- addon does not choose items or perform any actions; it only observes bags.
-- Read-only requested class spellbook and talent probes (no game actions).
local observedAbilityName = ''
local observedAbilityUntil = 0
SLASH_WOTLKBUDDYABILITY1 = '/wbtability'
SlashCmdList['WOTLKBUDDYABILITY'] = function(message)
    local value = safe(message or '', 48)
    if value ~= '' and string.match(value, '^[%a :]+$') then
        observedAbilityName = value
        observedAbilityUntil = GetTime() + 12
    else
        observedAbilityName = ''
        observedAbilityUntil = 0
    end
end

local observedTalentName = ''
SLASH_WOTLKBUDDYTALENT1 = '/wbttalent'
SlashCmdList['WOTLKBUDDYTALENT'] = function(message)
    local value = safe(message or '', 48)
    if value ~= '' and string.match(value, '^[%a :]+$') then
        observedTalentName = value
    else
        observedTalentName = ''
    end
end

local trackedQuestItemId = 0
SLASH_WOTLKBUDDYTRACK1 = '/wbttrack'
SlashCmdList['WOTLKBUDDYTRACK'] = function(message)
    local id = tonumber(message or '')
    if not id or id ~= math.floor(id) or id < 0 or id > 999999 then return end
    trackedQuestItemId = id
end

-- BEGIN SPELLBOOK PROBE HELPER
-- Legacy WotLK clients can expose GetSpellName but lack
-- GetSpellBookItemInfo. Use the exact spellbook slot plus GetSpellLink's
-- spell ID as a fallback; never infer a spell ID from its name alone.
local function resolveSpellbook(name)
    if name == '' then return 0, 0, 'NO_REQUEST', 0 end
    local bookType = BOOKTYPE_SPELL or 'spell'
    if not GetSpellName and not GetSpellInfo then
        return 0, 0, 'NO_NAME_API', 0
    end
    local bestIndex, bestId, matches = 0, 0, 0
    local tabsAvailable = GetNumSpellTabs and GetSpellTabInfo
    local scanned = 0

    local function inspect(index)
        if index < 1 or index > 500 then return end
        local spellName = GetSpellName and GetSpellName(index, bookType)
        if not spellName and GetSpellInfo then
            spellName = GetSpellInfo(index, bookType)
        end
        if spellName ~= name then return end
        matches = matches + 1
        local id = nil
        if GetSpellBookItemInfo then
            local kind, itemId = GetSpellBookItemInfo(index, bookType)
            if (kind == 'SPELL' or kind == 'spell') and
                    type(itemId) == 'number' then
                id = itemId
            end
        end
        local validBookId = type(id) == 'number' and
            id > 0 and id == math.floor(id) and id < 2147483648 and
            GetSpellInfo and GetSpellInfo(id) == name
        if not validBookId and GetSpellLink then
            local link, linkedId = GetSpellLink(index, bookType)
            if type(linkedId) == 'number' then
                id = linkedId
            elseif type(link) == 'string' then
                id = tonumber(string.match(link, '|Hspell:(%d+)'))
            end
        end
        -- A linked or book-item ID must resolve back to the exact spell
        -- name. This prevents placing a wrong spell from a mismatched slot.
        if type(id) == 'number' and id == math.floor(id) and
                id > 0 and id < 2147483648 and GetSpellInfo and
                GetSpellInfo(id) == name then
            bestIndex, bestId = index, id
        end
    end

    if tabsAvailable then
        for tab = 1, math.min(12, GetNumSpellTabs() or 0) do
            local _, _, offset, count = GetSpellTabInfo(tab)
            local first = math.max(1, (offset or 0) + 1)
            local last = math.min(500, (offset or 0) + (count or 0))
            for index = first, last do
                scanned = scanned + 1
                inspect(index)
            end
        end
    end
    -- Some private 3.3.5a builds do not expose usable spell tabs. Search
    -- a bounded number of actual book slots instead (read-only).
    if scanned == 0 or matches == 0 then
        for index = 1, 160 do inspect(index) end
    end
    if bestIndex > 0 then return bestIndex, bestId, 'OK', matches end
    if matches > 0 then return 0, 0, 'NO_ID', matches end
    return 0, 0, 'NO_MATCH', 0
end
-- END SPELLBOOK PROBE HELPER

-- BEGIN ACTION SPELL IDENTITY AUDIT
-- Read-only: an action's numeric GetActionInfo value is NOT treated as
-- an authoritative spell ID on legacy/modified 3.3.5a clients. Compare
-- both a database spell ID and an exact player spellbook slot separately.
-- The icon checks are diagnostic only and never authorize key presses.
local function auditActionIdentity(actionSlot, rawId)
    local function query(func, ...)
        if type(func) ~= 'function' then return nil, nil, nil end
        local ok, a, b, c = pcall(func, ...)
        if not ok then return nil, nil, nil end
        return a, b, c
    end
    local bookType = BOOKTYPE_SPELL or 'spell'
    local rawName, _, rawIcon = query(GetSpellInfo, rawId)
    local bookName, _, bookIcon = query(GetSpellInfo, rawId, bookType)
    if not bookName then
        bookName = query(GetSpellName, rawId, bookType)
    end
    local link, linkedId = query(GetSpellLink, rawId, bookType)
    if type(linkedId) ~= 'number' and type(link) == 'string' then
        linkedId = tonumber(string.match(link, '|Hspell:(%d+)'))
    end
    local itemKind, itemId = query(GetSpellBookItemInfo, rawId, bookType)
    local resolved = 0
    for pass = 1, 2 do
        local candidate = pass == 1 and linkedId or itemId
        if type(candidate) == 'number' and candidate > 0 and
                candidate == math.floor(candidate) and
                candidate < 2147483648 and bookName then
            local candidateName = query(GetSpellInfo, candidate)
            if candidateName == bookName and
                    (candidate == linkedId or itemKind == 'SPELL' or
                     itemKind == 'spell') then
                resolved = candidate
                break
            end
        end
    end
    local actionIcon = query(GetActionTexture, actionSlot)
    local function matchIcon(expected)
        if not actionIcon or not expected then return '-1' end
        return tostring(actionIcon) == tostring(expected) and '1' or '0'
    end
    if not bookIcon and resolved > 0 then
        local _, _, resolvedIcon = query(GetSpellInfo, resolved)
        bookIcon = resolvedIcon
    end
    return rawName or '', bookName or '', resolved,
        matchIcon(rawIcon), matchIcon(bookIcon)
end
-- BEGIN VERIFIED ACTION SPELL RESOLVER
-- On stock clients the raw GetActionInfo value can be a spell database ID.
-- On this actual 3.3.5a client it is a spellBOOK slot, e.g. raw 9 -> 133.
-- Both interpretations must agree with the *visible action icon* and an
-- independently named spell. A conflict or absent icon is not a safe action.
local function resolveActionSpell(actionIndex, rawId)
    if type(rawId) ~= 'number' or rawId < 1 or
            rawId >= 2147483648 or rawId ~= math.floor(rawId) then
        return nil, nil
    end
    local dbName, bookName, bookId, dbIconMatch, bookIconMatch =
        auditActionIdentity(actionIndex, rawId)
    local dbId = dbIconMatch == '1' and dbName ~= '' and rawId or nil
    local slotId = bookIconMatch == '1' and bookName ~= '' and
        type(bookId) == 'number' and bookId > 0 and bookId or nil
    -- Shared spell textures can obscure identity: never choose a winner
    -- when two *different* valid spell IDs both match the icon.
    if dbId and slotId and dbId ~= slotId then
        return nil, nil
    end
    local spellId = slotId or dbId
    local name = slotId and bookName or dbName
    if not spellId or name == '' or not GetSpellInfo then
        return nil, nil
    end
    local ok, authoritativeName = pcall(GetSpellInfo, spellId)
    if not ok or authoritativeName ~= name then
        return nil, nil
    end
    return spellId, name
end
-- END VERIFIED ACTION SPELL RESOLVER

-- END ACTION SPELL IDENTITY AUDIT

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
    -- Optional, read-only WorldFrame hover: a name is only exported if the
    -- cursor is over the 3-D world, not a bag/chat/tooltip/action bar region.
    -- Python correlates this with a double-observed GO template/GUID and
    -- world distance before permitting any right-click.
    local hoveredWorldName = ''
    if GetMouseFocus and GetMouseFocus() == WorldFrame and
            GameTooltip and GameTooltip:IsShown() and
            GameTooltipTextLeft1 then
        hoveredWorldName = safe(GameTooltipTextLeft1:GetText(), 75)
    end
    local targeting = SpellIsTargeting and SpellIsTargeting() and '1' or '0'
    appendLine(table.concat({'G', hoveredWorldName, targeting}, '\t'))
    -- Prioritize explicitly requested spellbook probes ahead of optional
    -- quest detail, gear, and merchant records so a busy frame cannot
    -- silently drop the result. SD always acknowledges the request.
    if observedAbilityName ~= '' and GetTime() < observedAbilityUntil then
        local bookIndex, spellId, status, matches =
            resolveSpellbook(observedAbilityName)
        appendLine(table.concat({'SD', safe(observedAbilityName,48),
            status, tostring(matches)}, '\t'))
        if bookIndex > 0 then
            appendLine(table.concat({'SF', tostring(bookIndex),
                tostring(spellId), safe(observedAbilityName,48)}, '\t'))
        end
    end
    -- BEGIN ACTIONBAR OBSERVATIONS
    -- Main bar observations are *priority* telemetry. Publishing these
    -- before quest/gear/merchant rows prevents omitted AS/ES data from
    -- causing repeated placement. AB never authorizes an input: its last
    -- field says whether the slot's digit key was independently verified.
    -- The 1-0 keys operate on the CURRENT visible main bar page.
    -- The same physical keys can select a different set of underlying
    -- action indexes after changing page; never trust base indices alone.
    local page = GetActionBarPage and GetActionBarPage() or 0
    if type(page) ~= 'number' or page < 1 or page > 6 or
            page ~= math.floor(page) then page = 0 end
    local freeAction = 0
    appendLine('AH\t' .. (GetActionInfo and '1' or '0'))
    if GetActionInfo then
        for slot = 1, 10 do
            local actionIndex = page > 1 and ((page-1)*12+slot) or slot
            local keyOK = page > 0 and slotHasDigitBinding(slot)
            local kind, rawId = GetActionInfo(actionIndex)
            if not kind then
                if keyOK and freeAction == 0 then
                    freeAction = slot
                end
            elseif kind == 'spell' then
                local id, name = resolveActionSpell(actionIndex, rawId)
                if id and name then
                    appendLine(table.concat({'AB', tostring(slot),
                        tostring(id), safe(name,44),
                        keyOK and '1' or '0'}, '\t'))
                    if keyOK then
                        local usable = IsUsableAction and IsUsableAction(slot) and 1 or 0
                        local inRange = IsActionInRange and IsActionInRange(slot)
                        local range = inRange == nil and -1 or (inRange == 1 and 1 or 0)
                        local cooldown = 0
                        if GetActionCooldown then
                            local start, duration, enabled = GetActionCooldown(slot)
                            if enabled == 1 and type(start) == 'number' and
                                    type(duration) == 'number' then
                                cooldown = math.max(0, math.min(300000,
                                    math.floor(1000*(start+duration-GetTime()))))
                            end
                        end
                        appendLine(table.concat({'AS', tostring(slot),
                            tostring(id), safe(name,44), tostring(usable),
                            tostring(range), tostring(cooldown)}, '\t'))
                    end
                end
            end
        end
    end
    if freeAction > 0 then
        appendLine('ES\t' .. tostring(freeAction))
    end
    -- BEGIN RAW ACTIONBAR AUDIT
    -- Read-only raw actionbar inspection. WoW 3.3.5a can page the
    -- visible primary bar (12 buttons per page). GetActionInfo(7) may be
    -- different from the visible button 7 when another page is selected.
    -- These rows must NEVER authorize PlaceAction or a guessed key press.
    appendLine('APG\t' .. tostring(page))
    if GetActionInfo then
        for button = 1, 10 do
            for view = 1, (page > 1 and 2 or 1) do
                local index = view == 1 and button or ((page-1)*12+button)
                local kind, id = GetActionInfo(index)
                local name = ''
                if kind == 'spell' and type(id) == 'number' then
                    local _, resolvedName = resolveActionSpell(index, id)
                    name = resolvedName or ''
                elseif kind == 'macro' and GetActionText then
                    name = GetActionText(index) or ''
                end
                -- Emit empty positions too: an unreported slot must be
                -- distinguishable from a genuinely empty position.
                local row = table.concat({'AR', view == 1 and 'base' or 'visible',
                    tostring(button), tostring(index), safe(kind or 'empty',24),
                    type(id) == 'number' and tostring(math.floor(id)) or '0',
                    safe(name,44), slotHasDigitBinding(button) and '1' or '0'}, '\t')
                appendLine(row)
            end
        end
    end
    -- END RAW ACTIONBAR AUDIT
    -- BEGIN ACTION IDENTITY TELEMETRY
    -- AX is purely diagnostic. Keep AS / legacy F unchanged until the
    -- user confirms which interpretation actually matches visible spells.
    if GetActionInfo then
        for button = 1, 10 do
            local index = page > 1 and (page-1)*12+button or button
            local kind, rawId = GetActionInfo(index)
            if kind == 'spell' and type(rawId) == 'number' and
                    rawId > 0 and rawId < 2147483648 then
                local dbName, bookName, bookId, dbIconOK, bookIconOK =
                    auditActionIdentity(index, rawId)
                appendLine(table.concat({'AX', tostring(button),
                    tostring(index), tostring(rawId),
                    safe(dbName,44), safe(bookName,44),
                    tostring(bookId), dbIconOK, bookIconOK}, '\t'))
            end
        end
    end
    -- END ACTION IDENTITY TELEMETRY
    -- END ACTIONBAR OBSERVATIONS
    -- Inventory tracking is read only. Match ItemId exactly against source
    -- Horde XML and report current inventory count in each fresh frame.
    if trackedQuestItemId > 0 and GetItemCount then
        local counted = GetItemCount(trackedQuestItemId, false)
        if type(counted) == 'number' and counted >= 0 and
                counted == math.floor(counted) and counted <= 200000 then
            -- GetItemInfo may initially return nil until the client caches
            -- the item. Keep reporting the ID and quantity, but never
            -- invent a journal item name when the lookup is unavailable.
            local itemName = GetItemInfo and GetItemInfo(trackedQuestItemId)
            appendLine(table.concat({'J', tostring(trackedQuestItemId),
                tostring(counted), safe(itemName, 65)}, '\t'))
        end
    end
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
    -- Combat observation. Creature entry is encoded in the 3.3.5a UnitGUID
    -- as 0xF130 + six hex digits for entry + six for spawn ID. Never infer it
    -- from localized NPC names or the currently selected target label.
    local targetGuid = UnitExists('target') and UnitGUID('target') or ''
    local targetEntry = 0
    if type(targetGuid) == 'string' and string.match(targetGuid, '^0x[Ff]130%x%x%x%x%x%x%x%x%x%x%x%x$') then
        targetEntry = tonumber(string.sub(targetGuid, 7, 12), 16) or 0
    end
    local targetHealth = UnitExists('target') and (UnitHealth('target') or 0) or 0
    local targetMax = UnitExists('target') and (UnitHealthMax('target') or 0) or 0
    local targetDead = UnitExists('target') and UnitIsDead('target') and 1 or 0
    local canAttack = UnitExists('target') and UnitCanAttack('player', 'target') and 1 or 0
    local targetRange = -1
    if UnitExists('target') and IsSpellInRange then
        local ok, value = pcall(IsSpellInRange, 'Fireball', 'target')
        if ok and value ~= nil then targetRange = value == 1 and 1 or 0 end
    end
    -- A hostile target is authorized for *defensive* combat only when
    -- target-of-target resolves to this exact player GUID, not merely because
    -- it is an attackable unit near the travel path.
    local attackingPlayer = '-1'
    if UnitExists('target') and UnitExists('targettarget') then
        attackingPlayer = UnitGUID('targettarget') == guid and '1' or '0'
    end
    -- Reaction 1..3 is an actively hostile (red) NPC faction; reaction 4
    -- is neutral (yellow) and must not be preemptively attacked.
    local reaction = UnitExists('target') and UnitReaction and
        UnitReaction('target', 'player') or nil
    if type(reaction) ~= 'number' or reaction < 1 or reaction > 8 then
        reaction = 0
    end
    appendLine(table.concat({'T', tostring(targetEntry), tostring(targetHealth),
        tostring(targetMax), tostring(targetDead), tostring(canAttack),
        tostring(targetRange), attackingPlayer, tostring(reaction)}, '\t'))
    local function known(spellId)
        if not IsSpellKnown then return -1 end
        local ok, value = pcall(IsSpellKnown, spellId)
        if not ok or value == nil then return -1 end
        return value and 1 or 0
    end
    local arcane = 0
    local armor = 0
    for i = 1, 40 do
        local name = UnitBuff('player', i)
        if not name then break end
        if name == 'Arcane Intellect' then arcane = 1 end
        if name == 'Frost Armor' or name == 'Ice Armor' or name == 'Mage Armor' then armor = 1 end
    end
    appendLine(table.concat({'B', tostring(known(1459)), tostring(arcane),
        tostring(known(168)), tostring(armor), tostring(known(133))}, '\t'))

    -- Read-only Mage sustenance telemetry: exact known conjured item IDs and
    -- verified bag/slot for the best available stack. The addon NEVER casts
    -- conjuration or uses food; the armed external controller decides.
    local conjuredFood = {5349, 1113, 1114, 1487, 8075, 8076, 22895, 22019}
    local conjuredWater = {5350, 2288, 2136, 3772, 8077, 8078, 8079, 30703, 22018}
    local function scanConjured(ids)
        local rankById = {}
        for rank, id in ipairs(ids) do rankById[id] = rank end
        local total, bestRank, bestBag, bestSlot, bestItem = 0, 0, -1, 0, 0
        for bag = 0, 4 do
            local slots = GetContainerNumSlots(bag) or 0
            for slot = 1, slots do
                local link = GetContainerItemLink(bag, slot)
                local id = link and tonumber(string.match(link, 'item:(%d+)'))
                local rank = id and rankById[id] or nil
                if rank then
                    local _, qty, locked = GetContainerItemInfo(bag, slot)
                    if type(qty) == 'number' and qty > 0 and not locked then
                        total = total + qty
                        if rank > bestRank then
                            bestRank, bestBag, bestSlot, bestItem = rank, bag, slot, id
                        end
                    end
                end
            end
        end
        return math.min(5000, total), bestBag, bestSlot, bestItem
    end
    local foodCount, foodBag, foodSlot, foodItem = scanConjured(conjuredFood)
    local waterCount, waterBag, waterSlot, waterItem = scanConjured(conjuredWater)
    appendLine(table.concat({'X', tostring(known(587)), tostring(known(5504)),
        tostring(foodCount), tostring(waterCount),
        tostring(foodBag), tostring(foodSlot), tostring(foodItem),
        tostring(waterBag), tostring(waterSlot), tostring(waterItem)}, '\t'))
    -- Fast, observable Fireball input. Never guess an action-bar slot:
    -- export a digit key ONLY when its bound button actually holds the
    -- Fireball spell (not a macro or unrelated action). Cast timing is
    -- measured from UnitCastingInfo, not projectile impact guesses.
    local fireballKey = '-'
    local fireballName = GetSpellInfo and GetSpellInfo(133)
    if fireballName and GetActionInfo and page > 0 then
        for button = 1, 10 do
            local actionIndex = page > 1 and ((page-1)*12+button) or button
            local kind, rawId = GetActionInfo(actionIndex)
            if kind == 'spell' and slotHasDigitBinding(button) then
                local spellId, name = resolveActionSpell(actionIndex, rawId)
                if spellId == 133 and name == fireballName then
                    fireballKey = button == 10 and '0' or tostring(button)
                    break
                end
            end
        end
    end
    local remaining = -1
    if UnitCastingInfo then
        local castName, _, _, _, startTime, endTime =
            UnitCastingInfo('player')
        if castName == fireballName and type(endTime) == 'number' then
            remaining = math.max(0, math.min(20000,
                math.floor(endTime - GetTime() * 1000)))
        end
    end
    appendLine(table.concat({'F', fireballKey, tostring(remaining)}, '\t'))
    local lootCount = 0
    if LootFrame and LootFrame:IsShown() and GetNumLootItems then
        lootCount = math.min(GetNumLootItems() or 0, 16)
    end
    appendLine(table.concat({'L', tostring(lootCount),
        (LootFrame and LootFrame:IsShown()) and '1' or '0'}, '\t'))
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
        elseif QuestFrameGreetingPanel and QuestFrameGreetingPanel:IsShown() then
            dialog = 'greeting'
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
    -- Use the *currently shown* quest panel's text only if the client API
    -- does not supply a title. Do not reuse stale text from an invisible panel.
    if dialogQuestTitle == '' and dialog == 'progress' and QuestProgressTitleText
            and QuestProgressTitleText:IsShown() then
        dialogQuestTitle = safe(QuestProgressTitleText:GetText(), 100)
    elseif dialogQuestTitle == '' and (dialog == 'detail' or dialog == 'reward')
            and QuestInfoTitleHeader and QuestInfoTitleHeader:IsShown() then
        dialogQuestTitle = safe(QuestInfoTitleHeader:GetText(), 100)
    end
    local rewardChoices, moneyRequired = -1, -1
    if dialog == 'reward' then
        if GetNumQuestChoices then rewardChoices = GetNumQuestChoices() or -1 end
        if GetQuestMoneyToGet then moneyRequired = GetQuestMoneyToGet() or 0 end
    end
    appendLine(table.concat({'I', dialog, tostring(dialogQuestID), dialogQuestTitle,
        tostring(rewardChoices), tostring(moneyRequired)}, '\t'))
    -- Gossip service options are distinct from available/active quest titles.
    -- 3.3.5a GetGossipOptions returns alternating (text, type) values.
    -- Export a COMPLETE numbered list only if it fits; Python must not
    -- guess that the first option is always training or merchant service.
    if dialog == 'gossip' and GetNumGossipOptions and GetGossipOptions then
        local count = GetNumGossipOptions() or 0
        if count >= 0 and count <= 32 then
            local pairValues = {GetGossipOptions()}
            local rows = {}
            local valid = true
            for i = 1, count do
                local name = pairValues[2*i - 1]
                local kind = pairValues[2*i]
                if type(name) ~= 'string' or name == '' or
                        type(kind) ~= 'string' or kind == '' then
                    valid = false
                    break
                end
                table.insert(rows, table.concat({'H', tostring(i),
                    safe(kind, 24), safe(name, 90)}, '\t'))
            end
            local needed = 7
            for _, row in ipairs(rows) do
                needed = needed + string.len(row) + 1
            end
            if valid and payloadBytes + needed <= CAPACITY - 20 then
                appendLine(table.concat({'K', tostring(count)}, '\t'))
                for _, row in ipairs(rows) do appendLine(row) end
            end
        end
    end
    -- Read actual reward options from the visible quest panel, rather than
    -- assuming quest IDs have the same items/order on every server. GetItemInfo
    -- may be uncached: report an unknown slot/level and let Python fail closed.
    if dialog == 'reward' and rewardChoices and rewardChoices > 0 and
            rewardChoices <= 12 and GetQuestItemInfo then
        for i = 1, rewardChoices do
            local ok, name, _, _, _, usable = pcall(GetQuestItemInfo, 'choice', i)
            if not ok then name, usable = nil, nil end
            local link = GetQuestItemLink and GetQuestItemLink('choice', i) or nil
            local itemId = link and tonumber(string.match(link, 'item:(%d+)')) or 0
            local itemLevel, equipSlot = -1, ''
            if link and GetItemInfo then
                local cached, _, _, _, level, _, _, _, _, slot =
                    pcall(GetItemInfo, link)
                if cached then
                    itemLevel = tonumber(level) or -1
                    equipSlot = safe(slot, 32)
                end
            end
            local usableFlag = usable == nil and '-1' or (usable and '1' or '0')
            if not appendLine(table.concat({'R', tostring(i), safe(name, 80),
                usableFlag, equipSlot, tostring(itemLevel), tostring(itemId)}, '\t')) then
                break
            end
        end
    end
    -- Completed quest history is intentionally profile-agnostic: emit observed
    -- server flags and recent journal IDs, not hard-coded quest 363.
    local completionStates = {}
    if completionCache then
        for id, done in pairs(completionCache) do
            if type(id) == 'number' and id > 0 and done then
                completionStates[id] = 1
            end
        end
    end
    if IsQuestFlaggedCompleted then
        for id in pairs(recentlyObservedQuestIds) do
            local ok, done = pcall(IsQuestFlaggedCompleted, id)
            if ok and done ~= nil then completionStates[id] = done and 1 or 0 end
        end
    end
    local completedIds = {}
    for id in pairs(completionStates) do table.insert(completedIds, id) end
    table.sort(completedIds)
    -- Bound the historical flag block. At early levels all history fits; on
    -- longer characters we prioritize recently seen quests first.
    local emitted = {}
    local recentIds = {}
    for id in pairs(recentlyObservedQuestIds) do table.insert(recentIds,id) end
    table.sort(recentIds)
    local count = 0
    for _, id in ipairs(recentIds) do
        if completionStates[id] ~= nil and count < 100 then
            appendLine(table.concat({'C', tostring(id), tostring(completionStates[id])}, '\t'))
            emitted[id] = true
            count = count + 1
        end
    end
    for _, id in ipairs(completedIds) do
        if not emitted[id] and count < 100 then
            appendLine(table.concat({'C', tostring(id), tostring(completionStates[id])}, '\t'))
            count = count + 1
        end
    end
    -- Find the exact on-screen button associated with each *active* quest.
    -- GossipTitleButtonN's frame N is NOT the same as its GetID(): available
    -- quests appear first, so a quest with active index 1 may be button 3.
    -- The frame is the one a human actually clicks. 0 = no unique visible match.
    local function activeButton(source, questIndex)
        local number = 0
        for i = 1, 32 do
            local button = _G[(source == 'gossip' and 'GossipTitleButton' or 'QuestTitleButton') .. i]
            if button and button:IsShown() and button:GetID() == questIndex and
                    ((source == 'gossip' and button.type == 'Active') or
                     (source == 'greeting' and button.isActive == 1)) then
                if number ~= 0 then return 0 end
                number = i
            end
        end
        return number
    end
    -- Active quest-list entries are different from the quest progress/reward
    -- panel. The 3.3.5a GossipFrame API returns four fields per active quest:
    -- title, level, isLowLevel, isComplete. QuestFrameGreetingPanel uses
    -- GetActiveTitle(i) -> title, isComplete. Report only active quest entries;
    -- never choose a quest from the available/new quest list by accident.
    if dialog == 'gossip' and GetNumGossipActiveQuests and GetGossipActiveQuests then
        local count = math.min(GetNumGossipActiveQuests() or 0, 32)
        local options = {GetGossipActiveQuests()}
        for i = 1, count do
            local title = options[(i - 1) * 4 + 1]
            local isComplete = options[(i - 1) * 4 + 4]
            if title then
                appendLine(table.concat({'A', 'gossip', tostring(i),
                    isComplete and '1' or '0', safe(title, 100),
                    tostring(activeButton('gossip', i))}, '\t'))
            end
        end
    elseif dialog == 'greeting' and GetNumActiveQuests and GetActiveTitle then
        local count = math.min(GetNumActiveQuests() or 0, 32)
        for i = 1, count do
            local title, isComplete = GetActiveTitle(i)
            if title then
                appendLine(table.concat({'A', 'greeting', tostring(i),
                    isComplete and '1' or '0', safe(title, 100),
                    tostring(activeButton('greeting', i))}, '\t'))
            end
        end
    end
    -- Available quests are separate from active turn-ins, and may share
    -- the same NPC. The actual on-screen button ID avoids indexing errors.
    local function availableButton(source, questIndex)
        local number = 0
        for n = 1, 32 do
            local button = _G[(source == 'gossip' and 'GossipTitleButton' or 'QuestTitleButton') .. n]
            if button and button:IsShown() and button:GetID() == questIndex and
                    ((source == 'gossip' and button.type == 'Available') or
                     (source == 'greeting' and button.isActive == 0)) then
                if number ~= 0 then return 0 end
                number = n
            end
        end
        return number
    end
    if dialog == 'gossip' and GetNumGossipAvailableQuests and GetGossipAvailableQuests then
        local count = math.min(GetNumGossipAvailableQuests() or 0, 32)
        local options = {GetGossipAvailableQuests()}
        for i = 1, count do
            local title = options[(i - 1) * 5 + 1]
            if title then
                appendLine(table.concat({'V', 'gossip', tostring(i), safe(title, 100),
                    tostring(availableButton('gossip', i))}, '\t'))
            end
        end
    elseif dialog == 'greeting' and GetNumAvailableQuests and GetAvailableTitle then
        local count = math.min(GetNumAvailableQuests() or 0, 32)
        for i = 1, count do
            local title = GetAvailableTitle(i)
            if title then
                appendLine(table.concat({'V', 'greeting', tostring(i), safe(title, 100),
                    tostring(availableButton('greeting', i))}, '\t'))
            end
        end
    end
    -- Completion-state records have already been emitted once above from the
    -- server-backed cache and recently observed quest IDs. Do not append
    -- legacy quest-363 rows: their value was undefined and duplicated IDs.
    local entries = GetNumQuestLogEntries() or 0
    for i = 1, entries do
        local title, level, _, _, isHeader, _, isComplete, _, questID = GetQuestLogTitle(i)
        if not isHeader and questID and questID > 0 then
            recentlyObservedQuestIds[questID] = true
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
    -- Optional bounded inventory scan for Mage cloth upgrades. No equipment
    -- changes are made by this addon. Do not truncate core quest telemetry when
    -- the inventory has too many records: Z=0 disables automatic gear actions.
    local armorSlots = {
        INVTYPE_HEAD=1, INVTYPE_SHOULDER=3, INVTYPE_CHEST=5,
        INVTYPE_ROBE=5, INVTYPE_WAIST=6, INVTYPE_LEGS=7,
        INVTYPE_FEET=8, INVTYPE_WRIST=9, INVTYPE_HAND=10,
        INVTYPE_CLOAK=15
    }
    local function armorInfo(link)
        if not link or not GetItemInfo then return nil, false end
        local name, _, _, level, req, category, subtype, _, equip =
            GetItemInfo(link)
        if not name then return nil, true end
        if category ~= 'Armor' or subtype ~= 'Cloth' or not armorSlots[equip] then
            return nil, false
        end
        local id = tonumber(string.match(link, 'item:(%d+)')) or 0
        if id < 1 then return nil, true end
        local stats = GetItemStats and GetItemStats(link) or {}
        if type(stats) ~= 'table' then stats = {} end
        -- Weight Mage primary attributes. Item level is only a tie-breaker.
        local score = math.floor(
            20*(stats.ITEM_MOD_SPELL_POWER_SHORT or 0) +
            10*(stats.ITEM_MOD_INTELLECT_SHORT or 0) +
            5*(stats.ITEM_MOD_SPIRIT_SHORT or 0) +
            3*(stats.ITEM_MOD_STAMINA_SHORT or 0) +
            6*(stats.ITEM_MOD_SPELL_HASTE_RATING_SHORT or 0) +
            6*(stats.ITEM_MOD_SPELL_CRIT_RATING_SHORT or 0) + 0.5)
        local canEquip = IsEquippableItem and IsEquippableItem(link) and 1 or 0
        return {id=id, name=safe(name, 55), ilvl=tonumber(level) or -1,
                req=tonumber(req) or -1, inv=armorSlots[equip],
                score=score, allowed=canEquip}, false
    end
    local rows = {}
    local gearComplete = 1
    for slot = 1, 19 do
        local link = GetInventoryItemLink and GetInventoryItemLink('player', slot)
        if link then
            local gear, unknown = armorInfo(link)
            if unknown then gearComplete = 0 end
            if gear and gear.inv == slot then
                table.insert(rows, table.concat({'W', tostring(slot),
                    tostring(gear.id), tostring(gear.ilvl),
                    tostring(gear.score)}, '\t'))
            end
        end
    end
    local found = 0
    for bag = 0, 4 do
        for slot = 1, GetContainerNumSlots(bag) or 0 do
            local link = GetContainerItemLink(bag, slot)
            if link then
                local item, unknown = armorInfo(link)
                if unknown then gearComplete = 0 end
                if item then
                    found = found + 1
                    if found <= 20 then
                        table.insert(rows, table.concat({'U', tostring(bag),
                            tostring(slot), tostring(item.id), tostring(item.inv),
                            tostring(item.ilvl), tostring(item.req),
                            tostring(item.score), tostring(item.allowed),
                            item.name}, '\t'))
                    else
                        gearComplete = 0
                    end
                end
            end
        end
    end
    local bytes = 4
    for _, row in ipairs(rows) do bytes = bytes + string.len(row) + 1 end
    if gearComplete == 1 and payloadBytes + bytes <= CAPACITY - 20 then
        for _, row in ipairs(rows) do appendLine(row) end
        appendLine('Z\t1')
    elseif payloadBytes + 4 <= CAPACITY - 20 then
        appendLine('Z\t0')
    end
    -- Merchant sales candidates are read only. Never emit bags, active quest
    -- items, soulbound equipment on the character, or item classes unknown to
    -- this 3.3.5 client. Never call UseContainerItem inside the addon.
    -- The Python controller verifies full frames twice, then right-clicks
    -- only one selected, verified slot and checks sale results.
    if MerchantFrame and MerchantFrame:IsShown() then
        local rows = {}
        -- The buyback tab can ignore right-click sales; require the normal
        -- merchant sell tab and a quest-item API before authorizing sales.
        local saleScanComplete = (
            GetContainerItemQuestInfo and
            (not MerchantFrame.selectedTab or MerchantFrame.selectedTab == 1)
            ) and 1 or 0
        local equipTypes = {
            INVTYPE_HEAD=1, INVTYPE_NECK=1, INVTYPE_SHOULDER=1,
            INVTYPE_BODY=1, INVTYPE_CHEST=1, INVTYPE_ROBE=1,
            INVTYPE_WAIST=1, INVTYPE_LEGS=1, INVTYPE_FEET=1,
            INVTYPE_WRIST=1, INVTYPE_HAND=1, INVTYPE_FINGER=1,
            INVTYPE_TRINKET=1, INVTYPE_CLOAK=1,
            INVTYPE_WEAPON=1, INVTYPE_2HWEAPON=1,
            INVTYPE_WEAPONMAINHAND=1, INVTYPE_WEAPONOFFHAND=1,
            INVTYPE_SHIELD=1, INVTYPE_HOLDABLE=1, INVTYPE_RANGED=1,
            INVTYPE_RANGEDRIGHT=1, INVTYPE_THROWN=1, INVTYPE_RELIC=1
        }
        if saleScanComplete == 1 then
            for bag = 0, 4 do
                for slot = 1, (GetContainerNumSlots(bag) or 0) do
                    local link = GetContainerItemLink(bag, slot)
                    if link then
                        local _, qty, locked = GetContainerItemInfo(bag, slot)
                        local id = tonumber(string.match(link, 'item:(%d+)'))
                        local name, _, quality, _, _, itemType, _,
                            _, equipLoc, _, price = GetItemInfo(link)
                        local isQuest = GetContainerItemQuestInfo(bag, slot)
                        -- Uncached item metadata must not authorize a sale.
                        if not (id and qty and quality and price and itemType
                                and equipLoc and name) then
                            saleScanComplete = 0
                        elseif (not isQuest and itemType ~= ITEM_CLASS_QUEST
                                and not locked and price > 0 and
                                (quality == 0 or
                                 (quality <= 2 and equipTypes[equipLoc]))) then
                            table.insert(rows, table.concat({
                                'D', tostring(bag), tostring(slot),
                                tostring(id), tostring(qty), tostring(quality),
                                tostring(price), '0', tostring(locked and 1 or 0),
                                safe(equipLoc, 27)}, '\t'))
                        end
                    end
                end
            end
        end
        if #rows > 64 then saleScanComplete = 0 end
        local needed = 12
        for _, row in ipairs(rows) do needed = needed + string.len(row) + 1 end
        if payloadBytes + needed > CAPACITY - 20 then
            saleScanComplete = 0
        end
        if saleScanComplete == 1 then
            appendLine(table.concat({'M','1',tostring(#rows)}, '\t'))
            for _, row in ipairs(rows) do appendLine(row) end
        else
            appendLine('M\t0\t0')
        end
    end
    -- Class trainer services are read only. Expose *available* services, not
    -- unavailable ranks, and require a complete frame before any purchase.
    if ClassTrainerFrame and ClassTrainerFrame:IsShown() and
            GetNumTrainerServices and GetTrainerServiceInfo then
        local total = math.min(GetNumTrainerServices() or 0, 120)
        local services = {}
        for i = 1, total do
            local name, rank, category = GetTrainerServiceInfo(i)
            if category == 'available' and name then
                local cost = GetTrainerServiceCost and GetTrainerServiceCost(i) or -1
                local required = GetTrainerServiceLevelReq and
                    GetTrainerServiceLevelReq(i) or -1
                table.insert(services, table.concat({'Y', tostring(i),
                    safe(name, 55), safe(rank, 25), tostring(cost or -1),
                    tostring(required or -1)}, '\t'))
            end
        end
        local needed = 6
        for _, row in ipairs(services) do needed = needed + string.len(row) + 1 end
        if payloadBytes + needed <= CAPACITY - 20 then
            appendLine(table.concat({'N','1',tostring(#services)}, '\t'))
            for _, row in ipairs(services) do appendLine(row) end
        end
    end
    -- Optional talent/cast/aura records follow the priority quest data.
    -- One controller-requested talent, resolved by *name* to the real
    -- tree/index on this 3.3.5a client. The addon NEVER spends points.
    if observedTalentName ~= '' and GetNumTalentTabs and
            GetNumTalents and GetTalentInfo then
        local found = false
        for tree = 1, GetNumTalentTabs() do
            for index = 1, GetNumTalents(tree) do
                local name, _, _, _, rank, maximum, _, learnable =
                    GetTalentInfo(tree,index)
                if name == observedTalentName and type(rank) == 'number' and
                        type(maximum) == 'number' and maximum > 0 then
                    local can = learnable == nil and -1 or (learnable and 1 or 0)
                    local row = table.concat({'TL',tostring(tree),
                        tostring(index),tostring(rank),tostring(maximum),
                        tostring(can),safe(name,48)}, '\t')
                    if payloadBytes + string.len(row) + 1 <= CAPACITY - 20 then
                        appendLine(row)
                    end
                    found = true
                    break
                end
            end
            if found then break end
        end
    end
    -- Generic cast and aura state, shared by all combat classes. These
    -- are observations only; the controller performs class-specific policy.
    local castName, _, _, _, _, castEnd = UnitCastingInfo('player')
    if type(castName) == 'string' and type(castEnd) == 'number' then
        local ms = math.max(0, math.min(20000, math.floor(castEnd-GetTime()*1000)))
        local record = table.concat({'CT', safe(castName,44),tostring(ms)}, '\t')
        if payloadBytes + string.len(record) + 1 <= CAPACITY - 20 then
            appendLine(record)
        end
    end
    for _, aura in ipairs({{'player','AP',UnitBuff}, {'target','AT',UnitDebuff}}) do
        if aura[3] and UnitExists(aura[1]) then
            for index = 1, 12 do
                local name = aura[3](aura[1], index)
                if not name then break end
                local record = table.concat({aura[2],safe(name,44)}, '\t')
                if payloadBytes + string.len(record) + 1 <= CAPACITY - 20 then
                    appendLine(record)
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

-- Native WoW Goal/Action heads-up display. Unlike an external Tk window,
-- this remains visible in exclusive-fullscreen WoW and is independent of the
-- RGB telemetry canvas. Python sets only an integer journal quest ID and a
-- bounded fixed phase using allowlisted slash commands. Live action text
-- (combat, loot, walking, casting) comes from the game itself.
local statusFrame = CreateFrame('Frame', 'WotLKBuddyStatusHUD', UIParent)
statusFrame:SetWidth(370)
statusFrame:SetHeight(53)
statusFrame:ClearAllPoints()
-- Anchor below the RGB telemetry grid near the top center-right.
-- Relative anchoring follows the grid across UI scales and resolutions,
-- leaving the upper-left player/party portrait area unobstructed.
statusFrame:SetPoint('TOP', canvas, 'BOTTOM', 0, -10)
statusFrame:SetFrameStrata('HIGH')
statusFrame:EnableMouse(false)
statusFrame:SetBackdrop({bgFile='Interface\\Buttons\\WHITE8X8'})
statusFrame:SetBackdropColor(0.04, 0.06, 0.10, 0.88)
local goalText = statusFrame:CreateFontString(nil, 'OVERLAY', 'GameFontNormal')
goalText:SetPoint('TOPLEFT', statusFrame, 'TOPLEFT', 9, -9)
goalText:SetWidth(352)
goalText:SetJustifyH('LEFT')
local actionText = statusFrame:CreateFontString(nil, 'OVERLAY', 'GameFontHighlightSmall')
actionText:SetPoint('TOPLEFT', goalText, 'BOTTOMLEFT', 0, -7)
actionText:SetWidth(352)
actionText:SetJustifyH('LEFT')

local hudQuestId = 0
local hudQuestName = ''
local hudPhase = 'idle'
local hudTravelKind = ''
local hudTravelName = ''
local hudLastAction = ''
local hudLastActive = 0
local phaseLabels = {
    idle='Waiting for next goal',
    hunt='Complete quest objectives',
    turnin='Turn in completed quest',
    quests='Accept available quests',
    train='Visit class trainer',
    gear='Check and equip upgrades',
    vendor='Visit merchant'
}
local function setQuestGoal(id)
    if hudQuestId ~= id then
        hudTravelKind, hudTravelName = '', ''
    end
    hudQuestId = id
    hudQuestName = ''
    if id > 0 and GetNumQuestLogEntries and GetQuestLogTitle then
        for i=1, GetNumQuestLogEntries() do
            local title, _, _, _, isHeader, _, _, _, qid =
                GetQuestLogTitle(i)
            if not isHeader and qid == id and type(title) == 'string' then
                hudQuestName = title
                break
            end
        end
    end
    if id > 0 and hudQuestName == '' then
        hudQuestName = 'Quest ' .. tostring(id)
    end
end
SLASH_WOTLKBUDDYGOAL1 = '/wbtgoal'
SlashCmdList['WOTLKBUDDYGOAL'] = function(message)
    local id = tonumber(message or '')
    if not id or id ~= math.floor(id) or id < 0 or id > 999999 then return end
    setQuestGoal(id)
    statusFrame:Show()
end
SLASH_WOTLKBUDDYPHASE1 = '/wbtphase'
SlashCmdList['WOTLKBUDDYPHASE'] = function(message)
    if phaseLabels[message or ''] then
        if hudPhase ~= message then
            hudTravelKind, hudTravelName = '', ''
        end
        hudPhase = message
        statusFrame:Show()
    end
end

-- BEGIN HUD NAVIGATION CONTEXT
-- These are presentation-only strings. They never influence targeting,
-- movement, quest telemetry, combat, or spell casts.
local function travelActionLabel(kind, target, questName)
    if kind == 'enemies' then
        return 'Navigating to ' ..
            ((questName and questName ~= '' and questName) or 'quest') .. ' enemies'
    elseif kind == 'npc' and target and target ~= '' then
        return 'Navigating to ' .. target
    elseif kind == 'corpse' then
        return 'Navigating to quest corpse'
    elseif kind == 'firing' then
        return 'Navigating to firing position'
    end
    return nil
end

SLASH_WOTLKBUDDYTRAVEL1 = '/wbttravel'
SlashCmdList['WOTLKBUDDYTRAVEL'] = function(message)
    if message == 'clear' then
        hudTravelKind, hudTravelName = '', ''
    elseif message == 'enemies' or message == 'corpse' or
            message == 'firing' then
        hudTravelKind, hudTravelName = message, ''
    else
        -- Accept only a bounded NPC name, not an arbitrary Lua expression
        -- or command string. Names originate in validated quest profiles.
        local npc = string.match(message or '', "^npc ([A-Za-z][A-Za-z '%-]*)$")
        if not npc or string.len(npc) > 64 then return end
        hudTravelKind, hudTravelName = 'npc', npc
    end
    statusFrame:Show()
end
-- END HUD NAVIGATION CONTEXT

local statusElapsed = 0
statusFrame:SetScript('OnUpdate', function(_, elapsed)
    statusElapsed = statusElapsed + elapsed
    if statusElapsed < 0.20 then return end
    statusElapsed = 0
    if hudQuestId > 0 and hudQuestName == ('Quest '..hudQuestId) then
        setQuestGoal(hudQuestId)
    end
    local goal = phaseLabels[hudPhase] or phaseLabels.idle
    if hudQuestId > 0 then
        goal = goal .. ': ' .. hudQuestName
    end
    goalText:SetText('Goal: ' .. string.sub(goal, 1, 65))
    local action = phaseLabels[hudPhase] or 'Standing by'
    if LootFrame and LootFrame:IsShown() then
        action = 'Looting corpse'
    elseif ClassTrainerFrame and ClassTrainerFrame:IsShown() then
        action = 'Training Mage spells'
    elseif UnitCastingInfo and UnitCastingInfo('player') then
        local spell = UnitCastingInfo('player')
        action = 'Casting ' .. tostring(spell)
    elseif UnitChannelInfo and UnitChannelInfo('player') then
        local spell = UnitChannelInfo('player')
        action = 'Channeling ' .. tostring(spell)
    elseif UnitAffectingCombat('player') then
        action = 'Combat'
    elseif travelActionLabel(hudTravelKind, hudTravelName, hudQuestName) then
        -- Keep the actual destination visible during paused Detour movement,
        -- steering corrections, and proactive hostile scans. GetUnitSpeed
        -- drops to zero during those pauses and must not hide the goal.
        action = travelActionLabel(hudTravelKind, hudTravelName, hudQuestName)
    elseif GetUnitSpeed and (GetUnitSpeed('player') or 0) > 0.2 then
        action = travelActionLabel(
            hudPhase == 'hunt' and 'enemies' or '', '', hudQuestName
        ) or 'Navigating'
    elseif hudPhase == 'hunt' then
        action = 'Searching for quest enemies'
    elseif hudPhase == 'turnin' then
        action = 'Interacting with quest giver'
    end
    if action ~= hudLastAction then
        hudLastAction = action
        hudLastActive = GetTime()
        actionText:SetText('Action: ' .. string.sub(action, 1, 67))
    end
end)
statusFrame:Hide()
SLASH_WOTLKBUDDYHUD1 = '/wbthud'
SlashCmdList['WOTLKBUDDYHUD'] = function(message)
    if message == 'show' then statusFrame:Show()
    elseif message == 'hide' then statusFrame:Hide() end
end

queryCompletions()
publish()
