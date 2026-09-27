// Chạy file này một lần duy nhất sau khi MongoDB Atlas Local khởi động.
// Lệnh: mongosh "mongodb://root:root@localhost:27017/?authSource=admin" create_vector_index.js

use("lensvocab");

db.global_flashcards.createSearchIndex(
  "vocab_embedding_index",
  "vectorSearch",
  {
    fields: [
      {
        type: "vector",
        path: "embedding",
        numDimensions: 1024,
        similarity: "cosine",
      },
    ],
  }
);

print("✅ Vector Search Index 'vocab_embedding_index' created on global_flashcards.embedding");
