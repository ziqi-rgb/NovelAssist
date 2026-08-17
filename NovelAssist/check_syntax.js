var fs = require('fs');
var code = fs.readFileSync('NovelAssist/frontend/app.js', 'utf8');

var lines = code.split('\n');
var depth = { paren: 0, brace: 0, bracket: 0 };
var inString = false, inTemplate = false, inSingle = false, inComment = false;
var lastZeroParen = 0, lastZeroBrace = 0;

for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    for (var j = 0; j < line.length; j++) {
        var ch = line[j];
        var prev = j > 0 ? line[j-1] : '';
        
        if (inComment) {
            if (ch === '*' && line[j+1] === '/') { inComment = false; j++; }
            continue;
        }
        if (inString && !(ch === '"' && prev !== '\\')) continue;
        if (inSingle && !(ch === "'" && prev !== '\\')) continue;
        if (inTemplate && !(ch === '`' && prev !== '\\')) continue;
        if (inString && ch === '"' && prev !== '\\') { inString = false; continue; }
        if (inSingle && ch === "'" && prev !== '\\') { inSingle = false; continue; }
        if (inTemplate && ch === '`' && prev !== '\\') { inTemplate = false; continue; }
        
        if (ch === '/' && line[j+1] === '/') break;
        if (ch === '/' && line[j+1] === '*') { inComment = true; j++; continue; }
        if (ch === '"' && prev !== '\\') { inString = true; continue; }
        if (ch === "'" && prev !== '\\') { inSingle = true; continue; }
        if (ch === '`' && prev !== '\\') { inTemplate = true; continue; }
        
        if (ch === '(') depth.paren++;
        else if (ch === ')') depth.paren--;
        else if (ch === '{') depth.brace++;
        else if (ch === '}') depth.brace--;
        else if (ch === '[') depth.bracket++;
        else if (ch === ']') depth.bracket--;
        
        if (depth.paren === 0) lastZeroParen = i+1;
        if (depth.brace === 0) lastZeroBrace = i+1;
    }
}

console.log('Last line where parens balanced: ' + lastZeroParen);
console.log('Last line where braces balanced: ' + lastZeroBrace);
console.log('Final depth:', JSON.stringify(depth));

// Show lines after last balance point
var startLine = Math.min(lastZeroParen, lastZeroBrace) - 1;
console.log('\nLines from ' + startLine + ' to end:');
for (var i = Math.max(0, startLine); i < lines.length; i++) {
    console.log((i+1) + ': ' + lines[i].substring(0, 150));
}

