# TAO-Inspired Social Graph

A small demonstration of a graph-oriented social relationship layer inspired by the concepts behind large-scale social graph systems.

> This project does NOT recreate Meta TAO.

## Architecture

```text
                 Client
                   |
             Postman / Browser
                   |
                   v
                Express
                   |
                   v
          Social Graph Service
                   |
          +--------+--------+
          |                 |
          v                 v
    Graph Layer          SQLite
   adjacency lists    source of truth
          |
          v
    Social traversal