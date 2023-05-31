import os, sys, json
from flask import Flask, request, jsonify
from storage.mongo import MongoDbClientSingleton
import datasource.datasource_handler as datasource
from datasource.file_system import File, Directory, FileType, PdfFile, LinkFile
from flask_cors import CORS
from flask_socketio import SocketIO
from bson.objectid import ObjectId
from werkzeug.utils import secure_filename


# More setup information here: https://flask.palletsprojects.com/en/2.2.x/tutorial/factory/
def create_app():
    # create and configure the app
    app = Flask(__name__)

    # Enable CORS for local development
    CORS(app)
    socketio = SocketIO(
        app, cors_allowed_origins="*", async_mode="eventlet", engineio_logger=True
    )
    socketio.init_app(app)

    # @app.route("/query", methods=["POST"])
    # def query_index():
    #     """
    #     Request body should be a JSON object with the following format:
    #     {
    #         "query": "What is a summary of this document?"
    #     }
    #     """
    #     query_text = request.get_json(force=True)
    #     if query_text is None:
    #         return "No query text provided", 400
    #     return "Baby don't hurt me"

    @app.route("/ingest_file", methods=["POST"])
    def ingest_file():
        if "file" not in request.files:
            return "No file provided", 400

        file = request.files["file"]
        file.filename = secure_filename(file.filename)

        try:
            # Get the GridFS instance for the CORPUS database
            fs = MongoDbClientSingleton.get_document_fs()

            # Save the file to MongoDB and get its id
            file_id = fs.put(file)
            return jsonify({"file_id": str(file_id)}), 200
        except Exception as e:
            return f"Failed to store file: {str(e)}", 400

    # @app.route("/ingest", methods=["POST"])
    # def ingest_data_source():
    #     """
    #     Request body should be a JSON object with the following format:
    #     {
    #         "dataType": "FILE_UPLOAD",
    #         "data": {...}
    #     }
    #     """
    #     json_body = request.get_json(force=True)
    #     if json_body is None:
    #         return "No body provided", 400

    #     data_type = datasource.DataSourceType(json_body.get("dataType"))
    #     if data_type == datasource.DataSourceType.GOOGLE_DOCS:
    #         (result, reason) = datasource.DataSourceHandler.ingest_google_docs(
    #             json_body.data
    #         )
    #     elif data_type == datasource.DataSourceType.LINK:
    #         (result, reason) = datasource.DataSourceHandler.ingest_url(json_body.data)
    #     else:
    #         return "Unknown data type", 400

    #     if result:
    #         return "Success", 200
    #     else:
    #         return f"Failed to handle data type {reason}", 400

    @app.route("/get_files", methods=["GET"])
    def get_files():
        print("Retrieving all files", file=sys.stderr)
        file_system_collection = MongoDbClientSingleton.get_file_system_collection()
        all_documents = file_system_collection.find()
        output = []
        for document in all_documents:
            # MongoDB includes _id field which is not serializable, so we need to remove it
            document["id"] = str(document["_id"])
            # Convert the document to a File object
            file_type = FileType(document.get("type"))
            if file_type == FileType.DIRECTORY:
                file_obj = Directory.from_dict(document)
            elif file_type == FileType.PDF:
                file_obj = PdfFile.from_dict(document)
            elif file_type == FileType.LINK:
                file_obj = LinkFile.from_dict(document)
            else:
                file_obj = File.from_dict(document)
            output.append(file_obj.to_dict())
        print(f"Returning {len(output)} documents", file=sys.stderr)
        return jsonify(output), 200

    @app.route("/create_file", methods=["POST"])
    def create_file():
        print("Creating file system item", file=sys.stderr)
        data = request.get_json()
        # Convert the data to a File object
        file_type = FileType(data.get("type"))
        if file_type == FileType.DIRECTORY:
            print(data, file=sys.stderr)
            item = Directory.from_dict(data)
        elif file_type == FileType.PDF:
            item = PdfFile.from_dict(data)
        elif file_type == FileType.LINK:
            item = LinkFile.from_dict(data)
        else:
            item = File.from_dict(data)
        file_system_collection = MongoDbClientSingleton.get_file_system_collection()
        item_dict = item.to_dict()
        item_dict["_id"] = item_dict.pop("id")  # use item's 'id' as MongoDB '_id'
        result = file_system_collection.insert_one(item_dict)
        print(
            f"Inserted file system item with id: [{result.inserted_id}]",
            file=sys.stderr,
        )
        socketio.emit("file_system_update", item_dict)

        return f"Inserted file system item with id: [{result.inserted_id}]", 200

    @app.route("/update_file", methods=["POST"])
    def update_file():
        print("Updating file system item", file=sys.stderr)
        data = request.get_json()
        # Convert the data to a File object
        file_type = FileType(data.get("type"))
        if file_type == FileType.DIRECTORY:
            item = Directory.from_dict(data)
        elif file_type == FileType.PDF:
            item = PdfFile.from_dict(data)
        elif file_type == FileType.LINK:
            item = LinkFile.from_dict(data)
        else:
            item = File.from_dict(data)
        file_system_collection = MongoDbClientSingleton.get_file_system_collection()

        item_dict = item.to_dict()
        item_dict["_id"] = item_dict.pop("id")  # use item's 'id' as MongoDB '_id'

        result = file_system_collection.replace_one(
            {"_id": item_dict["_id"]}, item_dict, upsert=True
        )

        if result.upserted_id is not None:
            print(
                f"Inserted file system item with id: [{result.upserted_id}]",
                file=sys.stderr,
            )
            socketio.emit("file_system_update", item_dict)
            return f"Inserted file system item with id: [{result.upserted_id}]", 200
        elif result.modified_count > 0:
            print(
                f"Updated file system item with id: [{item_dict['_id']}]",
                file=sys.stderr,
            )
            socketio.emit("file_system_update", item_dict)
            return f"Updated file system item with id: [{item_dict['_id']}]", 200
        else:
            return "An error occurred during the update.", 500

    @app.route("/delete_file/<id>", methods=["DELETE"])
    def delete_file(id):
        print(f"Deleting file system item with id: {id}", file=sys.stderr)
        file_system_collection = MongoDbClientSingleton.get_file_system_collection()
        result = file_system_collection.delete_one({"_id": id})
        if result.deleted_count == 1:
            print(f"Deleted file system item with id: {id}", file=sys.stderr)
            socketio.emit("file_system_update", {"_id": id, "operation": "delete"})
            return f"Deleted file system item with id: {id}", 200
        else:
            return "No file item found with this id.", 404

    @socketio.on("connect")
    def connect():
        print("Client connected", file=sys.stderr)

    @socketio.on("disconnect")
    def disconnect():
        print("Client disconnected", file=sys.stderr)

    return app, socketio


if __name__ == "__main__":
    app, socketio = create_app()
    socketio.run(app, host="0.0.0.0")
