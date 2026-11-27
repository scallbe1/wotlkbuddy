-- Execute the exact production work HUD block with a deterministic clock.
local file=assert(io.open('addons/WotLKBuddyTelemetry/WotLKBuddyTelemetry.lua','r'))
local src=file:read('*a');file:close()
local code=assert(src:match('%-%- BEGIN HUD STATIONARY WORK\n(.-)\n%-%- END HUD STATIONARY WORK'))
local factory=assert(loadstring([[
return function()
    local time=0
    local value=''
    local color
    local shown=false
    local function GetTime() return time end
    local destinationText={}
    local font={SetPoint=function() end,SetWidth=function() end,
        SetJustifyH=function() end,
        SetText=function(_,s) value=s end,
        SetTextColor=function(_,r,g,b) color={r,g,b} end}
    local statusFrame={CreateFontString=function() return font end,
        Show=function() shown=true end}
    local SlashCmdList={}
]] .. code .. [[
    return SlashCmdList['WOTLKBUDDYWORK'],function(t)
        time=t
        updateWorkText()
        return value,color,shown
    end
end
]]))
local command,render=factory()()
command('routes 1 48')
local value,color,shown=render(2)
assert(shown and value=='1/48 | 2 sec | Last update 2 sec ago')
assert(color[2]==0.9)
command('routes 17 48')
value=render(5)
assert(value=='17/48 | 5 sec | Last update 3 sec ago')
command('routes 99 99;print(1)')
value,color=render(17)
assert(value=='17/48 | 17 sec | No script update for 15 sec')
assert(color[1]==1 and color[2]==0.65)
command('patrol 0 0')
value=render(18)
assert(value=='1 sec | Last update 1 sec ago')
command('clear 0 0')
assert(render(30)=='')
print('Native stationary work HUD semantics passed')
