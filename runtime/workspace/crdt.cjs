// Automerge is also used by Agora canvas clients. This worker never accesses files.
const A = require('@automerge/automerge');
const fs = require('node:fs');
function merge(request) {
	let doc = request.current ? A.load(Buffer.from(request.current, 'base64')) : A.from({ fileId: request.fileId, text: request.text });
	if (doc.fileId !== request.fileId || typeof doc.text !== 'string') throw Error('DOCUMENT_INVALID');
	if (typeof request.text === 'string' && doc.text !== request.text) {
		doc = A.change(doc, draft => A.updateText(draft, ['text'], request.text));
	}
	if (request.incoming) {
		const remote = A.load(Buffer.from(request.incoming, 'base64'));
		if (remote.fileId !== request.fileId || typeof remote.text !== 'string') throw Error('FILE_REPLACED');
		doc = A.merge(doc, remote);
	}
	if (Buffer.byteLength(doc.text) > 131072) throw Error('FILE_TOO_LARGE');
	const bytes = A.save(doc);
	if (bytes.length > 131072) throw Error('DOCUMENT_TOO_LARGE');
	return { document: Buffer.from(bytes).toString('base64'), content: Buffer.from(doc.text).toString('base64') };
}
try {
	const request = JSON.parse(fs.readFileSync(0, 'utf8'));
	process.stdout.write(JSON.stringify(merge(request)));
} catch (error) {
	process.stdout.write(JSON.stringify({ error: ['FILE_REPLACED', 'FILE_TOO_LARGE', 'DOCUMENT_TOO_LARGE'].includes(error.message) ? error.message : 'DOCUMENT_INVALID' }));
}
